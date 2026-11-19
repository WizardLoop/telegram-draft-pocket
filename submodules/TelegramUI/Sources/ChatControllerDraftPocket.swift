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
    func capturePendingInputClear() -> ChatInputClearGuard {
        let state = self.presentationInterfaceState.interfaceState
        let input = self.chatDisplayNode.textInputPanelNode?.inputTextState ?? state.composeInputState
        return ChatInputClearGuard(peerId: self.chatLocation.peerId, threadId: self.chatLocation.threadId, generation: self.draftPocketComposerGeneration, input: input, state: state)
    }

    func canApplyPendingInputClear(_ captured: ChatInputClearGuard, allowMarkedText: Bool = false) -> Bool {
        guard !self.draftPocketOperationInProgress,
              allowMarkedText || self.chatDisplayNode.textInputPanelNode?.draftPocketHasMarkedText != true else { return false }
        let state = self.presentationInterfaceState.interfaceState
        let input = self.chatDisplayNode.textInputPanelNode?.inputTextState ?? state.composeInputState
        return captured.matches(peerId: self.chatLocation.peerId, threadId: self.chatLocation.threadId, generation: self.draftPocketComposerGeneration, input: input, state: state)
    }

    /// Read the exact account's store. A pocket observation never silently adopts another
    /// controller's active-state token: doing so would turn a stale editor into a writer.
    func refreshDraftPocketCount(preservingTransferredComposer: Bool = false) {
        guard self.isNodeLoaded else { return }
        if self.draftPocketScope.update(peerId: self.chatLocation.peerId, threadId: self.chatLocation.threadId) {
            // Normal navigation creates a new composer visit, even if the body is
            // equal. Explicit forum-send routing keeps the same captured draft.
            if !preservingTransferredComposer { self.draftPocketComposerGeneration &+= 1 }
            self.draftPocketObservation.set(nil)
            self.draftPocketView = nil
            self.draftPocketHasActiveToken = false
            self.draftPocketExpectedActiveToken = nil
            self.draftPocketOperationInProgress = false
            self.chatDisplayNode.textInputPanelNode?.setDraftPocketInteractionLocked(false)
            self.chatDisplayNode.textInputPanelNode?.cancelDraftPocketTransfer()
            // A sheet belongs to the old scope, including its pending Undo callbacks.
            let controller = self.draftPocketController
            self.draftPocketController = nil
            controller?.dismiss()
            self.updateDraftPocketPresentation()
        }
        guard let peerId = self.chatLocation.peerId,
              peerId.namespace != Namespaces.Peer.SecretChat else { return }
        let threadId = self.chatLocation.threadId
        let scope = self.draftPocketScope.token
        DraftPocketControllerRegistry.register(self)
        self.draftPocketObservation.set((self.context.engine.peers.observeDraftPocket(peerId: peerId, threadId: threadId)
        |> deliverOnMainQueue).start(next: { [weak self] view in
            guard let self, self.draftPocketScope.matches(scope) else { return }
            if !self.draftPocketHasActiveToken {
                self.draftPocketExpectedActiveToken = view.activeStateToken
                self.draftPocketHasActiveToken = true
            }
            self.draftPocketView = view
            self.updateDraftPocketPresentation()
        }))
    }

    fileprivate var draftPocketSupported: Bool {
        guard let peerId = self.chatLocation.peerId, peerId.namespace != Namespaces.Peer.SecretChat,
              self.chatLocation.threadId != EngineMessage.newTopicThreadId,
              !self.isUpdatingChatLocationThread,
              self.subject?.isService != true,
              self.presentationInterfaceState.interfaceState.editMessage == nil,
              self.presentationInterfaceState.interfaceState.forwardMessageIds == nil,
              self.presentationInterfaceState.interfaceState.mediaDraftState == nil,
              self.presentationInterfaceState.interfaceState.postSuggestionState == nil,
              self.audioRecorderValue == nil, self.videoRecorderValue == nil,
              !self.draftPocketHasDraft || self.chatDisplayNode.textInputPanelNode?.richTextInputNode?.supportsExactInputSelection != false else { return false }
        return true
    }

    private var draftPocketUnsupportedNotice: String {
        let strings = DraftPocketStrings(self.presentationData.strings)
        return self.chatLocation.threadId == EngineMessage.newTopicThreadId ? strings.finishTopic : strings.unsupported
    }

    fileprivate var draftPocketHasWritableComposer: Bool {
        // The upstream panel selector also handles closed topics, inaccessible
        // chats, account restrictions and membership; a cached text node alone
        // does not imply that its composer is currently available for writing.
        guard let panel = self.chatDisplayNode.textInputPanelNode,
              self.chatDisplayNode.inputPanelNode === panel,
              !self.presentationInterfaceState.isNotAccessible,
              canSendMessagesToChat(self.presentationInterfaceState) else { return false }
        return true
    }

    private var draftPocketHasDraft: Bool {
        let input = self.chatDisplayNode.textInputPanelNode?.inputTextState ?? self.presentationInterfaceState.interfaceState.composeInputState
        return !input.isEmpty || self.presentationInterfaceState.interfaceState.replyMessageSubject != nil
    }

    private func updateDraftPocketPresentation() {
        self.chatDisplayNode.textInputPanelNode?.draftPocketCount = (self.draftPocketView?.items.count ?? 0) + (self.draftPocketView?.pendingSends.count ?? 0)
        self.draftPocketController?.update(items: self.draftPocketView?.items ?? [], deletedItems: self.draftPocketView?.deletedItems ?? [], pendingSends: self.draftPocketView?.pendingSends ?? [], error: self.draftPocketView?.error, canPutAside: self.draftPocketSupported && self.draftPocketHasWritableComposer && self.draftPocketHasDraft, busy: self.draftPocketOperationInProgress || self.draftPocketView == nil, mediaRecoveryUnavailable: self.draftPocketView?.mediaRecoveryUnavailable ?? false)
    }

    func openDraftPocket() {
        guard self.draftPocketController == nil, !self.draftPocketOperationInProgress else { return }
        guard self.draftPocketSupported,
              self.chatDisplayNode.textInputPanelNode?.draftPocketHasMarkedText != true else {
            self.draftPocketNotice(self.draftPocketUnsupportedNotice)
            return
        }
        let wasFocused = self.chatDisplayNode.textInputPanelNode?.isFocused ?? false
        // Read-back before presenting commits the editor's structural selection to the host mirror.
        if self.draftPocketHasWritableComposer, let panel = self.chatDisplayNode.textInputPanelNode {
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
        controller.recoverSend = { [weak self] id in self?.recoverDraftPocketSend(id) }
        controller.closed = { [weak self, weak controller] in
            guard let self, self.draftPocketController === controller else { return }
            self.draftPocketController = nil
            if wasFocused && !self.draftPocketOperationInProgress && self.draftPocketHasWritableComposer && self.traceVisibility() { self.chatDisplayNode.textInputPanelNode?.ensureFocused() }
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
              view.error == nil, self.draftPocketHasActiveToken else {
            self.draftPocketNotice(self.draftPocketUnsupportedNotice)
            return
        }
        guard self.draftPocketHasWritableComposer, let panel = self.chatDisplayNode.textInputPanelNode else {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).writingUnavailable)
            return
        }
        guard !panel.draftPocketHasMarkedText else {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).unsupported)
            return
        }
        if !activeChecked {
            let threadId = self.chatLocation.threadId
            let scope = self.draftPocketScope.token
            let _ = (self.context.engine.peers.getOpaqueChatInterfaceState(peerId: peerId, threadId: threadId)
            |> deliverOnMainQueue).startStandalone(next: { [weak self] stored in
                guard let self, self.draftPocketScope.matches(scope),
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
            let scope = self.draftPocketScope.token
            let _ = (self.context.engine.data.get(TelegramEngine.EngineData.Item.Messages.Message(id: reply.messageId))
            |> deliverOnMainQueue).startStandalone(next: { [weak self] message in
                guard let self, self.draftPocketScope.matches(scope) else { return }
                self.transferDraftPocket(restoring: id, activeChecked: true, replyChecked: true, replyAvailable: message != nil)
            })
            return
        }
        let threadId = self.chatLocation.threadId
        let scope = self.draftPocketScope.token
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
                guard panel.richTextInputNode?.canRestoreInputSelection(item.snapshot.selection, in: item.snapshot.resolvedContent) != false else {
                    self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).unsupported)
                    return
                }
                let stored = try item.snapshot.interfaceState()
                guard let data = stored.opaqueData else { throw DraftPocketError.corruptStorage }
                let restored = try EngineDecoder.decodeValidated(ChatInterfaceState.self, from: data)
                var restoredInput = ChatTextInputState(content: item.snapshot.resolvedContent, selectionRange: 0 ..< 0)
                restoredInput.selection = item.snapshot.selection
                // Composer fields travel; scroll position, selection mode and unrelated chat state do not.
                nextState = captured.withUpdatedComposeInputState(restoredInput)
                    .withUpdatedReplyMessageSubject(replyAvailable ? restored.replyMessageSubject : nil)
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
        guard let participants = DraftPocketControllerRegistry.lock(self, captured: captured, restoring: nextState.composeInputState) else {
            self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).changed)
            return
        }
        panel.prepareDraftPocketTransfer()
        self.updateDraftPocketPresentation()
        // Keep the account/store alive until its transaction finishes, even if the controller leaves.
        let _ = (self.context.engine.peers.updateDraftPocket(peerId: peerId, threadId: threadId, expectedRevision: view.revision, expectedActiveState: self.draftPocketExpectedActiveToken, operation: operation)
        |> deliverOnMainQueue).startStandalone(next: { [weak self, weak panel] result in
            switch result {
            case let .failure(error):
                if let self, self.draftPocketScope.matches(scope) {
                    panel?.cancelDraftPocketTransfer()
                    self.draftPocketReport(error)
                }
                DraftPocketControllerRegistry.unlock(participants)
            case let .success(updatedView):
                // Apply only to participants from the captured visit. A stale result
                // must never replace a subsequently loaded composer or pocket view.
                for locked in participants where locked.isCurrent {
                    let participant = locked.controller
                    participant.draftPocketExpectedActiveToken = updatedView.activeStateToken
                    participant.draftPocketHasActiveToken = true
                    participant.draftPocketView = updatedView
                    // Invalidate delayed sends even when a restored card is byte-for-byte equal.
                    participant.draftPocketComposerGeneration &+= 1
                    participant.updateChatPresentationInterfaceState(animated: !UIAccessibility.isReduceMotionEnabled, interactive: true, { state in
                        state.updatedInterfaceState { current in
                            current.withUpdatedComposeInputState(nextState.composeInputState)
                                .withUpdatedReplyMessageSubject(nextState.replyMessageSubject)
                                .withUpdatedComposeDisableUrlPreviews(nextState.composeDisableUrlPreviews)
                                .withUpdatedInputLanguage(nextState.inputLanguage)
                                .withUpdatedSendMessageEffect(nextState.sendMessageEffect)
                        }
                    })
                    // A committed transfer is an intentional caret move, even when both
                    // cards have equal text. Ordinary layout echoes keep their own guard.
                    participant.chatDisplayNode.textInputPanelNode?.richTextInputNode?.restoreInputContent(
                        nextState.composeInputState.content, selection: nextState.composeInputState.selection)
                }
                DraftPocketControllerRegistry.unlock(participants)
                guard let self, self.draftPocketScope.matches(scope) else { return }
                let animateTransfer = { [weak self, weak panel] in
                    guard let self, self.draftPocketScope.matches(scope) else { return }
                    guard self.traceVisibility() else {
                        panel?.cancelDraftPocketTransfer()
                        return
                    }
                    guard self.draftPocketHasWritableComposer else {
                        panel?.cancelDraftPocketTransfer()
                        self.draftPocketNotice(DraftPocketStrings(self.presentationData.strings).writingUnavailable)
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
            if let self, self.draftPocketScope.matches(scope) { self.updateDraftPocketPresentation() }
        })
    }

    private func recoverDraftPocketSend(_ id: String) {
        guard !self.draftPocketOperationInProgress, !self.isUpdatingChatLocationThread,
              self.chatLocation.threadId != EngineMessage.newTopicThreadId, let view = self.draftPocketView,
              view.error == nil, view.pendingSends.contains(where: { $0.id == id }),
              self.chatLocation.peerId != nil else { return }
        let scope = self.draftPocketScope.token
        self.draftPocketOperationInProgress = true
        self.updateDraftPocketPresentation()
        let _ = (self.context.engine.peers.recoverDraftPocketSend(operationId: id, expectedRevision: view.revision)
        |> deliverOnMainQueue).startStandalone(next: { [weak self] result in
            guard let self, self.draftPocketScope.matches(scope) else { return }
            self.draftPocketOperationInProgress = false
            switch result {
            case let .success(view): self.draftPocketView = view
            case let .failure(error): self.draftPocketReport(error)
            }
            self.updateDraftPocketPresentation()
        })
    }

    private func deleteDraftPocketItem(_ id: String) {
        let scope = self.draftPocketScope.token
        self.mutateDraftPocket(.delete(id: id)) { [weak self] in
            guard let self else { return }
            self.present(UndoOverlayController(presentationData: self.presentationData, content: .removedChat(context: self.context, title: NSAttributedString(string: DraftPocketStrings(self.presentationData.strings).deleted), text: nil), elevatedLayout: false, action: { [weak self] action in
                guard let self, self.draftPocketScope.matches(scope) else { return false }
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
        guard !self.draftPocketOperationInProgress, !self.isUpdatingChatLocationThread,
              self.chatLocation.threadId != EngineMessage.newTopicThreadId, let view = self.draftPocketView, view.error == nil,
              let peerId = self.chatLocation.peerId else { return }
        // A persisted recovery row and the transient Undo overlay can both resolve the
        // same deletion. A late overlay callback must not report a spurious failure.
        switch operation {
        case let .undoDelete(id), let .finalizeDelete(id):
            guard view.deletedItems.contains(where: { $0.id == id }) else { return }
        default:
            break
        }
        let threadId = self.chatLocation.threadId
        let scope = self.draftPocketScope.token
        self.draftPocketOperationInProgress = true
        self.updateDraftPocketPresentation()
        let _ = (self.context.engine.peers.updateDraftPocket(peerId: peerId, threadId: threadId, expectedRevision: view.revision, expectedActiveState: view.activeStateToken, operation: operation)
        |> deliverOnMainQueue).startStandalone(next: { [weak self] result in
            guard let self, self.draftPocketScope.matches(scope) else { return }
            self.draftPocketOperationInProgress = false
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
    struct LockedController {
        let controller: ChatControllerImpl
        let scope: ChatInputScopeTracker.Token?

        var isCurrent: Bool { self.controller.draftPocketScope.matches(self.scope) }
    }

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

    static func lock(_ owner: ChatControllerImpl, captured: ChatInterfaceState, restoring inputToRestore: ChatTextInputState) -> [LockedController]? {
        assert(Thread.isMainThread)
        self.register(owner)
        let participants = self.controllers.compactMap { $0.value }.filter {
            $0.context.account.id == owner.context.account.id && $0.chatLocation.peerId == owner.chatLocation.peerId && $0.chatLocation.threadId == owner.chatLocation.threadId
        }
        for controller in participants {
            let state = controller.presentationInterfaceState.interfaceState
            let panel = controller.chatDisplayNode.textInputPanelNode
            let input = panel?.inputTextState ?? state.composeInputState
            guard !controller.draftPocketOperationInProgress,
                  controller.draftPocketSupported, controller.draftPocketHasWritableComposer,
                  controller.chatDisplayNode.textInputPanelNode?.draftPocketHasMarkedText != true,
                  (input.isEmpty && state.replyMessageSubject == nil) || controller.chatDisplayNode.textInputPanelNode?.richTextInputNode?.supportsExactInputSelection != false,
                  controller.chatDisplayNode.textInputPanelNode?.richTextInputNode?.canRestoreInputSelection(inputToRestore.selection, in: inputToRestore.content) != false,
                  state.matchesDraftPocketComposer(captured, input: input, effectiveInputLanguage: panel?.effectiveInputLanguage) else { return nil }
        }
        for controller in participants {
            controller.draftPocketOperationInProgress = true
            controller.chatDisplayNode.textInputPanelNode?.setDraftPocketInteractionLocked(true)
        }
        return participants.map { LockedController(controller: $0, scope: $0.draftPocketScope.token) }
    }

    static func unlock(_ participants: [LockedController]) {
        for locked in participants where locked.isCurrent {
            let controller = locked.controller
            controller.draftPocketOperationInProgress = false
            controller.chatDisplayNode.textInputPanelNode?.setDraftPocketInteractionLocked(false)
        }
    }
}
