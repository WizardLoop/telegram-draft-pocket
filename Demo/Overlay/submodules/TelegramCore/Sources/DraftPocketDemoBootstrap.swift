import Foundation
import Postbox
import SwiftSignalKit
import MtProtoKit

/// Optional demo overlay. This file is not needed by Draft Pocket in normal use.
/// No server account, login token, authorization key, or premium flag is fabricated.
public enum DraftPocketDemo {
    public static var buildMarker: String? {
        #if DEBUG && targetEnvironment(simulator)
        return "DRAFT_POCKET_ISOLATED_DEMO_V1"
        #else
        return nil
        #endif
    }

    public static var isEnabled: Bool {
        #if DEBUG && targetEnvironment(simulator)
        return CommandLine.arguments.contains("--draft-pocket-demo") && !CommandLine.arguments.contains("--ui-test")
        #else
        return false
        #endif
    }

    private static func argument(_ name: String) -> String? {
        let arguments = CommandLine.arguments
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
        return arguments[index + 1]
    }

    // A capture/test can choose a new namespace without deleting any previous data.
    public static var directoryName: String {
        let requested = self.argument("--draft-pocket-demo-data") ?? "manual-v1"
        let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
        precondition(!requested.isEmpty && requested.utf8.count <= 64 && requested.unicodeScalars.allSatisfy { allowed.contains($0) })
        return "draft-pocket-demo-" + requested
    }

    public static func basePath(appGroupPath: String) -> String {
        precondition(self.isEnabled)
        // Library survives normal app-group upgrades. A launch never erases this directory.
        return URL(fileURLWithPath: appGroupPath, isDirectory: true)
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent(self.directoryName, isDirectory: true).path
    }

    public static let accountPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(912340001))
    public static let conversationPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(912340002))
    public static let secondaryPeerId = PeerId(namespace: Namespaces.Peer.CloudUser, id: PeerId.Id._internalFromInt64Value(912340003))

    public static var launchPeerId: PeerId {
        return CommandLine.arguments.contains("--draft-pocket-demo-secondary") ? self.secondaryPeerId : self.conversationPeerId
    }

    private static var incomingQuestionScheduled = Set<String>()

    /// Fixture-only incoming history. Real typing is observed; composer/Pocket state is never edited.
    public static func observeComposer(account: Account, peerId: PeerId?, content: ChatInputContent) {
        #if DEBUG && targetEnvironment(simulator)
        guard self.isEnabled, Thread.isMainThread, peerId == self.conversationPeerId,
              URL(fileURLWithPath: account.basePath).pathComponents.contains(self.directoryName),
              content.plainText.hasPrefix("I’ve reviewed the new sticker pack."),
              content.plainText.contains("I’ll send the detailed notes shortly."),
              self.incomingQuestionScheduled.insert(account.basePath).inserted else { return }
        let _ = (account.postbox.transaction { transaction -> Void in
            let id = MessageId(peerId: self.conversationPeerId, namespace: Namespaces.Message.Cloud, id: 4)
            guard transaction.getMessage(id) == nil else { return }
            let message = StoreMessage(id: id, customStableId: nil, globallyUniqueId: nil,
                groupingKey: nil, threadId: nil, timestamp: Int32(Date().timeIntervalSince1970),
                flags: [.Incoming], tags: [], globalTags: [], localTags: [], forwardInfo: nil,
                authorId: self.conversationPeerId, text: "Quick question — can we publish at 18:00?",
                attributes: [], media: [])
            let _ = transaction.addMessages([message], location: .UpperHistoryBlock)
        }).startStandalone()
        #endif
    }

    public static func signIn(accountManager: AccountManager<TelegramAccountManagerTypes>, account: UnauthorizedAccount) -> Signal<Void, NoError> {
        #if DEBUG && targetEnvironment(simulator)
        // Guard the account itself as well as the launch flag. A normal account must never reach this write.
        precondition(self.isEnabled && URL(fileURLWithPath: account.rootPath).pathComponents.contains(self.directoryName))
        return account.postbox.transaction { transaction -> Void in
            initializedAppSettingsAfterLogin(transaction: transaction, appVersion: account.networkArguments.appVersion, syncContacts: false)
            self.seed(transaction: transaction)
            transaction.setState(AuthorizedAccountState(
                isTestingEnvironment: account.testingEnvironment,
                masterDatacenterId: account.masterDatacenterId,
                peerId: self.accountPeerId,
                state: AuthorizedAccountState.State(pts: 1, qts: 0, date: Int32(Date().timeIntervalSince1970), seq: 0),
                invalidatedChannels: []
            ))
        }
        |> mapToSignal { _ in
            accountManager.transaction { transaction -> Void in
                switchToAuthorizedAccount(transaction: transaction, account: account, isSupportUser: false)
            }
        }
        #else
        preconditionFailure("The offline fixture requires a debug simulator build")
        #endif
    }

    private static func makeUser(id: PeerId, name: String) -> TelegramUser {
        // Zero is a synthetic fixture-only hash. It permits ordinary local enqueue/RPC construction;
        // the inert engine never sends it, acknowledges it, or reports network delivery.
        return TelegramUser(id: id, accessHash: id == self.accountPeerId ? nil : .personal(0), firstName: name, lastName: nil, username: nil, phone: nil,
            photo: [], botInfo: nil, restrictionInfo: nil, flags: [], emojiStatus: nil, usernames: [],
            storiesHidden: nil, nameColor: nil, backgroundEmojiId: nil, profileColor: nil,
            profileBackgroundEmojiId: nil, subscriberCount: nil, verificationIconFileId: nil)
    }

    private static func seed(transaction: Transaction) {
        let peers: [Peer] = [self.makeUser(id: self.accountPeerId, name: "Demo"),
            self.makeUser(id: self.conversationPeerId, name: "@wizardloop"),
            self.makeUser(id: self.secondaryPeerId, name: "Casey (offline fixture)")]
        transaction.updatePeersInternal(peers, update: { _, updated in updated })
        transaction.updatePeerCachedData(peerIds: Set(peers.map { $0.id }), update: { _, _ in
            // A CloudUser history waits for BusinessIntro to become known, even when it has messages.
            // This is real cached data, not a bypass of ChatHistoryViewForLocation's readiness checks.
            return CachedUserData().withUpdatedBusinessIntro(nil).withUpdatedPeerStatusSettings(PeerStatusSettings())
                .withUpdatedAutoremoveTimeout(.known(nil)).withUpdatedPhoto(.known(nil))
                .withUpdatedPersonalPhoto(.known(nil)).withUpdatedFallbackPhoto(.known(nil))
        })
        self.seedConversation(transaction: transaction, peerId: self.conversationPeerId, messages: [
            ("Offline demo — fictional messages. Nothing is delivered to a Telegram account.", true),
            ("Could you review the new sticker pack?", true),
            ("I’m taking a look at the motion now.", false)
        ])
        self.seedConversation(transaction: transaction, peerId: self.secondaryPeerId, messages: [
            ("This separate fictional chat is used to check draft isolation.", true)
        ])
        for hole in transaction.allChatListHoles(groupId: .root) {
            transaction.replaceChatListHole(groupId: .root, index: hole.index, hole: nil)
        }
        // Pocket entries and the active composer are deliberately not seeded.
    }

    private static func seedConversation(transaction: Transaction, peerId: PeerId, messages: [(String, Bool)]) {
        let timestamp = Int32(Date().timeIntervalSince1970)
        let stored = messages.enumerated().map { index, value -> StoreMessage in
            StoreMessage(id: MessageId(peerId: peerId, namespace: Namespaces.Message.Cloud, id: Int32(index + 1)),
                customStableId: nil, globallyUniqueId: nil, groupingKey: nil, threadId: nil,
                timestamp: timestamp - Int32(messages.count - index) * 60,
                flags: value.1 ? [.Incoming] : [], tags: [], globalTags: [], localTags: [], forwardInfo: nil,
                authorId: value.1 ? peerId : self.accountPeerId,
                text: value.0, attributes: [], media: [])
        }
        let _ = transaction.addMessages(stored, location: .UpperHistoryBlock)
        transaction.removeHole(peerId: peerId, threadId: nil, namespace: Namespaces.Message.Cloud,
            space: .everywhere, range: 1 ... (Int32.max - 1))
        let topId = Int32(messages.count)
        transaction.resetIncomingReadStates([peerId: [Namespaces.Message.Cloud:
            .idBased(maxIncomingReadId: topId, maxOutgoingReadId: topId, maxKnownId: topId, count: 0, markedUnread: false)]])
        transaction.updatePeerChatListInclusion(peerId,
            inclusion: .ifHasMessagesOrOneOf(groupId: .root, pinningIndex: nil, minTimestamp: nil))
    }
}

// MTContext has its own auth/time-service connections outside NetworkEngine. The Network.swift
// hooks force these through a closed loopback proxy and disable backup-address/iCloud discovery.
let draftPocketDemoLoopbackProxy = MTSocksProxySettings(ip: "127.0.0.1", port: 1, username: nil, password: nil, secret: nil)

final class DraftPocketDemoNetworkEngine: NetworkEngine {
    let kind: NetworkEngineKind = .mtProtoKit

    func makeSession(datacenterId: Int, role: NetworkEngineSessionRole, usageCalculationInfo: MTNetworkUsageCalculationInfo?, delegate: NetworkEngineSessionDelegate?) -> NetworkEngineSession {
        return DraftPocketDemoNetworkSession(datacenterId: datacenterId, delegate: delegate)
    }
}

private final class DraftPocketDemoNetworkSession: NetworkEngineSession {
    let datacenterId: Int
    let requestService: NetworkEngineRequestService = DraftPocketDemoRequestService()
    private weak var delegate: NetworkEngineSessionDelegate?

    init(datacenterId: Int, delegate: NetworkEngineSessionDelegate?) {
        self.datacenterId = datacenterId
        self.delegate = delegate
    }

    func setPaused(_ paused: Bool) {
        // Report the real offline condition. Do not fake a connected session for a cleaner capture.
        self.delegate?.networkSessionConnectionStateChanged(NetworkEngineConnectionState(
            isNetworkAvailable: false, isConnected: false, isUpdatingConnectionContext: false,
            isPerformingServiceTasks: false, proxyAddress: nil, proxyHasConnectionIssues: false))
    }
    func setOnline(_ online: Bool) {}
    func addUpdateSink(_ sink: NetworkEngineUpdateSink) {}
    func stop() {}
    func movePendingRequests(to service: NetworkEngineRequestService, deadline: Double, completion: @escaping () -> Void) {
        // Live engine switching is refused by the demo's Network.swift guard.
        completion()
    }
}

private final class DraftPocketDemoRequestService: NetworkEngineRequestService {
    func add(_ request: NetworkEngineRequest) -> Disposable {
        // A retained, cancellable offline request; no socket and no synthetic RPC success.
        return ActionDisposable { withExtendedLifetime(request) {} }
    }
}
