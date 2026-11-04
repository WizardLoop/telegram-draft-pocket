import Foundation
import UIKit
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import AccountContext
import ChatInterfaceState
import ChatPresentationInterfaceState
import UndoUI

extension ChatControllerImpl {
    /// Read the exact account's store. A pocket observation never silently adopts another
    /// controller's active-state token: doing so would turn a stale editor into a writer.
    func refreshDraftPocketCount() {
        guard self.isNodeLoaded, let peerId = self.chatLocation.peerId,
              peerId.namespace != Namespaces.Peer.SecretChat else { return }
        let threadId = self.chatLocation.threadId
        DraftPocketControllerRegistry.register(self)
        self.draftPocketObservation.set((self.context.engine.peers.observeDraftPocket(peerId: peerId, threadId: threadId)
        |> deliverOnMainQueue).start(next: { [weak self] view in
            guard let self, self.chatLocation.peerId == peerId, self.chatLocation.threadId == threadId else { return }
            if !self.draftPocketHasActiveToken {
                self.draftPocketExpectedActiveToken = view.activeStateToken
                self.draftPocketHasActiveToken = true
            }
            self.draftPocketView = view
            self.updateDraftPocketPresentation()
        }))
    }

    private var draftPocketSupported: Bool {
        guard let peerId = self.chatLocation.peerId, peerId.namespace != Namespaces.Peer.SecretChat,
              self.subject?.isService != true,
              self.presentationInterfaceState.interfaceState.editMessage == nil,
              self.presentationInterfaceState.interfaceState.forwardMessageIds == nil,
              self.presentationInterfaceState.interfaceState.mediaDraftState == nil,
              self.presentationInterfaceState.interfaceState.postSuggestionState == nil,
              self.audioRecorderValue == nil, self.videoRecorderValue == nil,
              canSendMessagesToChat(self.presentationInterfaceState) else { return false }
        return true
    }

    private var draftPocketHasDraft: Bool {
        let input = self.chatDisplayNode.textInputPanelNode?.inputTextState ?? self.presentationInterfaceState.interfaceState.composeInputState
        return !input.isEmpty || self.presentationInterfaceState.interfaceState.replyMessageSubject != nil
    }

    private func updateDraftPocketPresentation() {
        self.chatDisplayNode.textInputPanelNode?.draftPocketCount = self.draftPocketView?.items.count ?? 0
        self.draftPocketController?.update(items: self.draftPocketView?.items ?? [], deletedItems: self.draftPocketView?.deletedItems ?? [], error: self.draftPocketView?.error, canPutAside: self.draftPocketSupported && self.draftPocketHasDraft, busy: self.draftPocketOperationInProgress || self.draftPocketView == nil)
    }

    func openDraftPocket() {
        guard self.draftPocketController == nil, !self.draftPocketOperationInProgress else { return }
        guard self.draftPocketSupported,
              self.chatDisplayNode.textInputPanelNode?.draftPocketHasMarkedText != true else {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).unsupported)
            return
        }
        let wasFocused = self.chatDisplayNode.textInputPanelNode?.isFocused ?? false
        // Read-back before presenting commits the editor's structural selection to the host mirror.
        if let panel = self.chatDisplayNode.textInputPanelNode {
            let input = panel.inputTextState
            self.updateChatPresentationInterfaceState(animated: false, interactive: false, { state in
                state.updatedInterfaceState { $0.withUpdatedComposeInputState(input).withUpdatedInputLanguage(panel.effectiveInputLanguage) }
            })
        }
        let controller = DraftPocketController(context: self.context)
        self.draftPocketController = controller
        controller.putAside = { [weak self] in self?.transferDraftPocket(restoring: nil) }
        controller.restore = { [weak self] id in self?.transferDraftPocket(restoring: id) }
        controller.deleteDraft = { [weak self] id in self?.deleteDraftPocketItem(id) }
        controller.undoDeleted = { [weak self] id in self?.mutateDraftPocket(.undoDelete(id: id)) {} }
        controller.finalizeDeleted = { [weak self] id in self?.mutateDraftPocket(.finalizeDelete(id: id)) {} }
        controller.closed = { [weak self, weak controller] in
            guard let self, self.draftPocketController === controller else { return }
            self.draftPocketController = nil
            if wasFocused && !self.draftPocketOperationInProgress && self.traceVisibility() { self.chatDisplayNode.textInputPanelNode?.ensureFocused() }
        }
        self.updateDraftPocketPresentation()
        self.present(controller, in: .window(.root))
    }

    private func draftPocketOpaqueState(_ state: ChatInterfaceState) throws -> OpaqueChatInterfaceState {
        return OpaqueChatInterfaceState(opaqueData: try EngineEncoder.encode(state), historyScrollMessageIndex: state.historyScrollMessageIndex, mediaDraftState: nil, synchronizeableInputState: state.synchronizeableInputState)
    }

    private func transferDraftPocket(restoring id: String?, activeChecked: Bool = false, replyChecked: Bool = false, replyAvailable: Bool = true) {
        guard !self.draftPocketOperationInProgress, self.draftPocketSupported,
              let peerId = self.chatLocation.peerId, let view = self.draftPocketView,
              view.error == nil, self.draftPocketHasActiveToken,
              let panel = self.chatDisplayNode.textInputPanelNode,
              !panel.draftPocketHasMarkedText else {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).unsupported)
            return
        }
        if !activeChecked {
            let threadId = self.chatLocation.threadId
            let _ = (self.context.engine.peers.getOpaqueChatInterfaceState(peerId: peerId, threadId: threadId)
            |> deliverOnMainQueue).startStandalone(next: { [weak self] stored in
                guard let self, self.chatLocation.peerId == peerId, self.chatLocation.threadId == threadId,
                      !self.draftPocketOperationInProgress else { return }
                let token: Data?
                do { token = try stored.map(draftPocketActiveStateToken) } catch {
                    self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).failed)
                    return
                }
                if token != self.draftPocketExpectedActiveToken {
                    let persisted = stored.map(ChatInterfaceState.parse) ?? ChatInterfaceState()
                    let current = self.presentationInterfaceState.interfaceState
                    let input = self.chatDisplayNode.textInputPanelNode?.inputTextState ?? current.composeInputState
                    // A newer token may be adopted only when this editor already displays the same
                    // draft. A differing unsaved editor never rebases its token over another writer.
                    guard input.content == persisted.composeInputState.content,
                          current.replyMessageSubject == persisted.replyMessageSubject,
                          current.composeDisableUrlPreviews == persisted.composeDisableUrlPreviews,
                          current.sendMessageEffect == persisted.sendMessageEffect else {
                        self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).changed)
                        return
                    }
                    self.draftPocketExpectedActiveToken = token
                }
                self.transferDraftPocket(restoring: id, activeChecked: true, replyChecked: replyChecked, replyAvailable: replyAvailable)
            })
            return
        }
        if let id, !replyChecked, let item = view.items.first(where: { $0.id == id }),
           let opaque = try? item.snapshot.interfaceState(), let reply = opaque.synchronizeableInputState?.replySubject {
            let _ = (self.context.engine.data.get(TelegramEngine.EngineData.Item.Messages.Message(id: reply.messageId))
            |> deliverOnMainQueue).startStandalone(next: { [weak self] message in
                self?.transferDraftPocket(restoring: id, activeChecked: true, replyChecked: true, replyAvailable: message != nil)
            })
            return
        }
        let threadId = self.chatLocation.threadId
        let liveInput = panel.inputTextState
        let captured = self.presentationInterfaceState.interfaceState
            .withUpdatedComposeInputState(liveInput)
            .withUpdatedInputLanguage(panel.effectiveInputLanguage)
            .withUpdatedTimestamp(Int32(Date().timeIntervalSince1970))
        let nextState: ChatInterfaceState
        let operation: DraftPocketOperation
        do {
            let current: DraftPocketSnapshot?
            if !liveInput.isEmpty || captured.replyMessageSubject != nil {
                current = try DraftPocketSnapshot(content: liveInput.content, selection: liveInput.selection, state: self.draftPocketOpaqueState(captured))
            } else {
                current = nil
            }
            if let id {
                guard let item = view.items.first(where: { $0.id == id }) else { return }
                let stored = try item.snapshot.interfaceState()
                guard let data = stored.opaqueData else { throw DraftPocketError.corruptStorage }
                let restored = try EngineDecoder.decode(ChatInterfaceState.self, from: data)
                var restoredInput = ChatTextInputState(content: item.snapshot.resolvedContent, selectionRange: 0 ..< 0)
                restoredInput.selection = item.snapshot.selection
                // Composer fields travel; scroll position, selection mode and unrelated chat state do not.
                nextState = captured.withUpdatedComposeInputState(restoredInput)
                    .withUpdatedReplyMessageSubject(restored.replyMessageSubject)
                    .withUpdatedComposeDisableUrlPreviews(restored.composeDisableUrlPreviews)
                    .withUpdatedInputLanguage(restored.inputLanguage)
                    .withUpdatedSendMessageEffect(restored.sendMessageEffect)
                operation = .restore(id: id, current: current, restoredState: try self.draftPocketOpaqueState(nextState))
            } else {
                guard let current else { return }
                nextState = captured.withUpdatedComposeInputState(ChatTextInputState())
                    .withUpdatedReplyMessageSubject(nil)
                    .withUpdatedComposeDisableUrlPreviews([])
                    .withUpdatedSendMessageEffect(nil)
                operation = .putAside(snapshot: current, clearedState: try self.draftPocketOpaqueState(nextState))
            }
        } catch {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).failed)
            return
        }
        guard let participants = DraftPocketControllerRegistry.lock(self, captured: captured) else {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).changed)
            return
        }
        panel.prepareDraftPocketTransfer()
        self.updateDraftPocketPresentation()
        // Keep the account/store alive until its transaction finishes, even if the controller leaves.
        let _ = (self.context.engine.peers.updateDraftPocket(peerId: peerId, threadId: threadId, expectedRevision: view.revision, expectedActiveState: self.draftPocketExpectedActiveToken, operation: operation)
        |> deliverOnMainQueue).startStandalone(next: { [weak self, weak panel] result in
            guard let self else {
                DraftPocketControllerRegistry.unlock(participants)
                return
            }
            switch result {
            case let .failure(error):
                panel?.cancelDraftPocketTransfer()
                DraftPocketControllerRegistry.unlock(participants)
                self.draftPocketReport(error)
            case let .success(updatedView):
                self.draftPocketExpectedActiveToken = updatedView.activeStateToken
                self.draftPocketHasActiveToken = true
                self.draftPocketView = updatedView
                for participant in participants {
                    participant.draftPocketExpectedActiveToken = updatedView.activeStateToken
                    participant.draftPocketHasActiveToken = true
                    participant.draftPocketView = updatedView
                    guard participant.chatLocation.peerId == peerId, participant.chatLocation.threadId == threadId else { continue }
                    participant.updateChatPresentationInterfaceState(animated: !UIAccessibility.isReduceMotionEnabled, interactive: true, { state in
                        state.updatedInterfaceState { current in
                            current.withUpdatedComposeInputState(nextState.composeInputState)
                                .withUpdatedReplyMessageSubject(nextState.replyMessageSubject)
                                .withUpdatedComposeDisableUrlPreviews(nextState.composeDisableUrlPreviews)
                                .withUpdatedInputLanguage(nextState.inputLanguage)
                                .withUpdatedSendMessageEffect(nextState.sendMessageEffect)
                        }
                    })
                }
                DraftPocketControllerRegistry.unlock(participants)
                let animateTransfer = { [weak self, weak panel] in
                    guard let self, self.traceVisibility(), self.chatLocation.peerId == peerId, self.chatLocation.threadId == threadId else {
                        panel?.cancelDraftPocketTransfer()
                        return
                    }
                    panel?.animateDraftPocketTransfer(restoring: id != nil)
                    panel?.ensureFocused()
                    if !replyAvailable { self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).unavailableReply) }
                }
                if let controller = self.draftPocketController {
                    controller.dismiss(completion: animateTransfer)
                } else {
                    animateTransfer()
                }
            }
            self.updateDraftPocketPresentation()
        })
    }

    private func deleteDraftPocketItem(_ id: String) {
        self.mutateDraftPocket(.delete(id: id)) { [weak self] in
            guard let self else { return }
            self.present(UndoOverlayController(presentationData: self.presentationData, content: .removedChat(context: self.context, title: NSAttributedString(string: DraftPocketStrings(self.presentationData.strings).deleted), text: nil), elevatedLayout: false, action: { [weak self] action in
                guard let self else { return false }
                if action == .undo {
                    self.mutateDraftPocket(.undoDelete(id: id)) {}
                    return true
                } else if action == .commit {
                    self.mutateDraftPocket(.finalizeDelete(id: id)) {}
                    return true
                }
                return false
            }), in: .window(.root))
        }
    }

    private func mutateDraftPocket(_ operation: DraftPocketOperation, completion: @escaping () -> Void) {
        guard !self.draftPocketOperationInProgress, let view = self.draftPocketView, view.error == nil,
              let peerId = self.chatLocation.peerId else { return }
        let threadId = self.chatLocation.threadId
        self.draftPocketOperationInProgress = true
        self.updateDraftPocketPresentation()
        let _ = (self.context.engine.peers.updateDraftPocket(peerId: peerId, threadId: threadId, expectedRevision: view.revision, expectedActiveState: view.activeStateToken, operation: operation)
        |> deliverOnMainQueue).startStandalone(next: { [weak self] result in
            guard let self else { return }
            self.draftPocketOperationInProgress = false
            guard self.chatLocation.peerId == peerId, self.chatLocation.threadId == threadId else { return }
            switch result {
            case let .success(view): self.draftPocketView = view; completion()
            case let .failure(error): self.draftPocketReport(error)
            }
            self.updateDraftPocketPresentation()
        })
    }

    private func draftPocketReport(_ error: DraftPocketError) {
        let strings = DraftPocketStrings(self.presentationData.strings)
        switch error {
        case .full: self.draftPocketNotice(strings.full)
        case .conflict: self.draftPocketNotice(strings.changed)
        case .unsupportedContext: self.draftPocketNotice(strings.unsupported)
        case .mediaRetentionUnavailable: self.draftPocketNotice(strings.mediaUnavailable)
        default: self.draftPocketNotice(strings.failed)
        }
    }

    private func draftPocketNotice(_ text: String) {
        self.present(UndoOverlayController(presentationData: self.presentationData, content: .info(title: nil, text: text, timeout: nil, customUndoText: nil), action: { _ in false }), in: .window(.root))
    }
}

/// A second window must not later save the pre-transfer composer over the committed result.
/// This registry contains no draft storage; Postbox remains the only durable source of truth.
private enum DraftPocketControllerRegistry {
    private final class WeakController {
        weak var value: ChatControllerImpl?
        init(_ value: ChatControllerImpl) { self.value = value }
    }
    private static var controllers: [WeakController] = []

    static func register(_ controller: ChatControllerImpl) {
        assert(Thread.isMainThread)
        self.controllers.removeAll { $0.value == nil }
        if !self.controllers.contains(where: { $0.value === controller }) {
            self.controllers.append(WeakController(controller))
        }
    }

    static func lock(_ owner: ChatControllerImpl, captured: ChatInterfaceState) -> [ChatControllerImpl]? {
        assert(Thread.isMainThread)
        self.register(owner)
        let participants = self.controllers.compactMap { $0.value }.filter {
            $0.context.account.id == owner.context.account.id && $0.chatLocation.peerId == owner.chatLocation.peerId && $0.chatLocation.threadId == owner.chatLocation.threadId
        }
        for controller in participants {
            let state = controller.presentationInterfaceState.interfaceState
            let input = controller.chatDisplayNode.textInputPanelNode?.inputTextState ?? state.composeInputState
            guard !controller.draftPocketOperationInProgress,
                  controller.chatDisplayNode.textInputPanelNode?.draftPocketHasMarkedText != true,
                  state.editMessage == nil, state.mediaDraftState == nil,
                  input == captured.composeInputState,
                  state.replyMessageSubject == captured.replyMessageSubject,
                  state.composeDisableUrlPreviews == captured.composeDisableUrlPreviews else { return nil }
        }
        for controller in participants {
            controller.draftPocketOperationInProgress = true
            controller.chatDisplayNode.textInputPanelNode?.setDraftPocketInteractionLocked(true)
        }
        return participants
    }

    static func unlock(_ participants: [ChatControllerImpl]) {
        for controller in participants {
            controller.draftPocketOperationInProgress = false
            controller.chatDisplayNode.textInputPanelNode?.setDraftPocketInteractionLocked(false)
        }
    }
}
