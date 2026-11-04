import Foundation
import UIKit
import AsyncDisplayKit
import Display
import SwiftSignalKit
import TelegramCore
import TelegramPresentationData
import AccountContext

/// A native, accessible list; the preview is deliberately never used to restore a draft.
final class DraftPocketController: ViewController, UITableViewDataSource, UITableViewDelegate {
    private let context: AccountContext
    private var presentationData: PresentationData
    private let presentationDisposable = MetaDisposable()
    private let tableView = UITableView(frame: .zero, style: .plain)
    private let titleLabel = UILabel()
    private let detailLabel = UILabel()
    private let emptyLabel = UILabel()
    private let putAsideButton = UIButton(type: .system)
    private let doneButton = UIButton(type: .system)
    private var items: [DraftPocketItem] = []
    private var deletedItems: [DraftPocketItem] = []
    private var pendingSends: [DraftPocketSendRecovery] = []
    private var lastLayout: ContainerViewLayout?
    private var canPutAside = false
    private var busy = false
    private var storageError: DraftPocketError?
    private var mediaRecoveryUnavailable = false

    var putAside: (() -> Void)?
    var restore: ((String) -> Void)?
    var deleteDraft: ((String) -> Void)?
    var undoDeleted: ((String) -> Void)?
    var finalizeDeleted: ((String) -> Void)?
    var recoverSend: ((String) -> Void)?
    var closed: (() -> Void)?

    init(context: AccountContext) {
        self.context = context
        self.presentationData = context.sharedContext.currentPresentationData.with { $0 }
        super.init(navigationBarPresentationData: nil)
        self.navigationPresentation = .modal
        self.presentationDisposable.set((context.sharedContext.presentationData
        |> deliverOnMainQueue).start(next: { [weak self] data in
            self?.presentationData = data
            self?.updateAppearance()
        }))
    }

    required init(coder: NSCoder) { preconditionFailure() }

    deinit { self.presentationDisposable.dispose() }

    override func loadDisplayNode() {
        self.displayNode = ASDisplayNode()
        self.displayNode.view.addSubview(self.tableView)
        for view in [self.titleLabel, self.detailLabel, self.emptyLabel, self.putAsideButton, self.doneButton] {
            self.displayNode.view.addSubview(view)
        }
        self.tableView.dataSource = self
        self.tableView.delegate = self
        self.tableView.rowHeight = UITableView.automaticDimension
        self.tableView.estimatedRowHeight = 110.0
        self.tableView.tableFooterView = UIView()
        self.tableView.keyboardDismissMode = .none
        self.titleLabel.font = UIFont.preferredFont(forTextStyle: .headline)
        self.detailLabel.font = UIFont.preferredFont(forTextStyle: .footnote)
        self.emptyLabel.font = UIFont.preferredFont(forTextStyle: .body)
        for label in [self.titleLabel, self.detailLabel, self.emptyLabel] {
            label.numberOfLines = 0
            label.adjustsFontForContentSizeCategory = true
            label.textAlignment = .natural
        }
        self.emptyLabel.textAlignment = .center
        self.putAsideButton.titleLabel?.font = UIFont.preferredFont(forTextStyle: .body)
        self.putAsideButton.titleLabel?.adjustsFontForContentSizeCategory = true
        self.putAsideButton.accessibilityIdentifier = "DraftPocket.PutAside"
        self.doneButton.accessibilityIdentifier = "DraftPocket.Close"
        self.putAsideButton.addTarget(self, action: #selector(self.putAsidePressed), for: .touchUpInside)
        self.doneButton.addTarget(self, action: #selector(self.closePressed), for: .touchUpInside)
        self.updateAppearance()
    }

    func update(items: [DraftPocketItem], deletedItems: [DraftPocketItem], pendingSends: [DraftPocketSendRecovery], error: DraftPocketError?, canPutAside: Bool, busy: Bool, mediaRecoveryUnavailable: Bool = false) {
        self.items = items
        self.deletedItems = deletedItems
        self.pendingSends = pendingSends
        self.storageError = error
        self.mediaRecoveryUnavailable = mediaRecoveryUnavailable
        self.canPutAside = canPutAside
        self.busy = busy
        guard self.isNodeLoaded else { return }
        self.emptyLabel.isHidden = error == nil && !(items.isEmpty && deletedItems.isEmpty && pendingSends.isEmpty)
        let strings = DraftPocketStrings(self.presentationData.strings)
        self.emptyLabel.text = error == nil ? strings.empty : strings.storageUnavailable
        self.detailLabel.text = strings.localOnly + (mediaRecoveryUnavailable ? "\n" + strings.mediaRecoveryUnavailable : "")
        self.putAsideButton.isEnabled = canPutAside && !busy && error == nil
        self.tableView.isUserInteractionEnabled = !busy
        self.tableView.reloadData()
    }

    private func updateAppearance() {
        guard self.isNodeLoaded else { return }
        let theme = self.presentationData.theme
        let strings = DraftPocketStrings(self.presentationData.strings)
        self.displayNode.view.semanticContentAttribute = self.presentationData.strings.baseLanguageCode.hasPrefix("he") || self.presentationData.strings.baseLanguageCode.hasPrefix("ar") ? .forceRightToLeft : .unspecified
        self.displayNode.backgroundColor = theme.list.plainBackgroundColor
        self.tableView.backgroundColor = theme.list.plainBackgroundColor
        self.tableView.separatorColor = theme.list.itemPlainSeparatorColor
        self.titleLabel.textColor = theme.list.itemPrimaryTextColor
        self.detailLabel.textColor = theme.list.itemSecondaryTextColor
        self.emptyLabel.textColor = theme.list.itemSecondaryTextColor
        self.putAsideButton.tintColor = theme.list.itemAccentColor
        self.doneButton.tintColor = theme.list.itemAccentColor
        self.titleLabel.text = strings.title
        self.detailLabel.text = strings.localOnly
        self.emptyLabel.text = strings.empty
        self.putAsideButton.setTitle(strings.putAside, for: .normal)
        self.doneButton.setTitle(self.presentationData.strings.Common_Done, for: .normal)
        self.update(items: self.items, deletedItems: self.deletedItems, pendingSends: self.pendingSends, error: self.storageError, canPutAside: self.canPutAside, busy: self.busy, mediaRecoveryUnavailable: self.mediaRecoveryUnavailable)
        if let layout = self.lastLayout { self.containerLayoutUpdated(layout, transition: .immediate) }
    }

    override func containerLayoutUpdated(_ layout: ContainerViewLayout, transition: ContainedViewLayoutTransition) {
        super.containerLayoutUpdated(layout, transition: transition)
        self.lastLayout = layout
        let inset = max(20.0, layout.safeInsets.left + 12.0)
        let width = layout.size.width - inset - max(20.0, layout.safeInsets.right + 12.0)
        let top = layout.insets(options: []).top + 12.0
        let doneWidth = max(60.0, self.doneButton.sizeThatFits(CGSize(width: width * 0.4, height: 100.0)).width)
        let titleSize = self.titleLabel.sizeThatFits(CGSize(width: width - doneWidth - 12.0, height: 1000.0))
        let isRTL = self.displayNode.view.effectiveUserInterfaceLayoutDirection == .rightToLeft
        self.titleLabel.frame = CGRect(x: isRTL ? inset + doneWidth + 12.0 : inset, y: top, width: width - doneWidth - 12.0, height: max(44.0, titleSize.height))
        self.doneButton.frame = CGRect(x: isRTL ? inset : inset + width - doneWidth, y: top, width: doneWidth, height: max(44.0, titleSize.height))
        let detailSize = self.detailLabel.sizeThatFits(CGSize(width: width, height: 1000.0))
        self.detailLabel.frame = CGRect(x: inset, y: self.titleLabel.frame.maxY + 8.0, width: width, height: detailSize.height)
        let buttonHeight = max(48.0, self.putAsideButton.sizeThatFits(CGSize(width: width, height: 1000.0)).height + 16.0)
        self.putAsideButton.frame = CGRect(x: inset, y: self.detailLabel.frame.maxY + 8.0, width: width, height: buttonHeight)
        let tableTop = self.putAsideButton.frame.maxY + 8.0
        self.tableView.frame = CGRect(x: 0.0, y: tableTop, width: layout.size.width, height: max(0.0, layout.size.height - tableTop))
        self.tableView.contentInset.bottom = layout.intrinsicInsets.bottom
        self.emptyLabel.frame = CGRect(x: inset, y: tableTop + 40.0, width: width, height: self.emptyLabel.sizeThatFits(CGSize(width: width, height: 1000.0)).height)
    }

    @objc private func putAsidePressed() { if !self.busy { self.putAside?() } }
    @objc private func closePressed() { self.dismiss() }

    override func viewDidDisappear(_ animated: Bool) {
        super.viewDidDisappear(animated)
        self.closed?()
    }

    func numberOfSections(in tableView: UITableView) -> Int { self.pendingSends.isEmpty ? (self.deletedItems.isEmpty ? 1 : 2) : 3 }
    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int { section == 0 ? self.items.count : (section == 1 ? self.deletedItems.count : self.pendingSends.count) }
    func tableView(_ tableView: UITableView, titleForHeaderInSection section: Int) -> String? {
        let strings = DraftPocketStrings(self.presentationData.strings)
        if section == 2 { return strings.interruptedSends }
        return section == 1 && !self.deletedItems.isEmpty ? strings.pendingDelete : nil
    }
    private func item(at indexPath: IndexPath) -> DraftPocketItem {
        return indexPath.section == 0 ? self.items[indexPath.row] : self.deletedItems[indexPath.row]
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let strings = DraftPocketStrings(self.presentationData.strings)
        let snapshot: DraftPocketSnapshot
        let id: String
        let actionTitle: String
        if indexPath.section == 2 {
            let recovery = self.pendingSends[indexPath.row]
            snapshot = recovery.snapshot
            id = recovery.id
            actionTitle = strings.recoverSend
        } else {
            let item = self.item(at: indexPath)
            snapshot = item.snapshot
            id = item.id
            actionTitle = indexPath.section == 0 ? strings.restore : strings.undoDelete
        }
        let cell = UITableViewCell(style: .subtitle, reuseIdentifier: nil)
        let preview = snapshot.content.plainText
        cell.textLabel?.text = preview.isEmpty ? strings.structured : String(preview.prefix(220))
        cell.textLabel?.font = UIFont.preferredFont(forTextStyle: .body)
        cell.textLabel?.numberOfLines = 4
        cell.textLabel?.adjustsFontForContentSizeCategory = true
        var details: [String] = []
        if let state = try? snapshot.interfaceState(), state.synchronizeableInputState?.replySubject != nil { details.append(strings.reply) }
        if draftPocketContainsMedia(snapshot.content) { details.append(strings.media) }
        cell.detailTextLabel?.text = details.isEmpty ? actionTitle : details.joined(separator: " · ")
        cell.detailTextLabel?.font = UIFont.preferredFont(forTextStyle: .footnote)
        cell.detailTextLabel?.adjustsFontForContentSizeCategory = true
        cell.textLabel?.textColor = self.presentationData.theme.list.itemPrimaryTextColor
        cell.detailTextLabel?.textColor = self.presentationData.theme.list.itemSecondaryTextColor
        cell.backgroundColor = self.presentationData.theme.list.itemBlocksBackgroundColor
        cell.accessoryType = .disclosureIndicator
        cell.accessibilityTraits = [.button]
        cell.accessibilityIdentifier = indexPath.section == 0 ? "DraftPocket.Restore.\(id)" : (indexPath.section == 1 ? "DraftPocket.UndoDelete.\(id)" : "DraftPocket.RecoverSend.\(id)")
        cell.accessibilityHint = actionTitle
        if indexPath.section != 2 {
            cell.accessibilityCustomActions = [UIAccessibilityCustomAction(name: strings.delete, actionHandler: { [weak self] _ in
                if indexPath.section == 0 { self?.deleteDraft?(id) } else { self?.finalizeDeleted?(id) }
                return true
            })]
        }
        return cell
    }

    func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        guard !self.busy else { return }
        if indexPath.section == 2 {
            self.recoverSend?(self.pendingSends[indexPath.row].id)
            return
        }
        let id = self.item(at: indexPath).id
        if indexPath.section == 0 { self.restore?(id) } else { self.undoDeleted?(id) }
    }

    func tableView(_ tableView: UITableView, trailingSwipeActionsConfigurationForRowAt indexPath: IndexPath) -> UISwipeActionsConfiguration? {
        // Recovery remains durable until a real enqueue or explicit recovery to a card.
        guard indexPath.section != 2 else { return nil }
        let id = self.item(at: indexPath).id
        let action = UIContextualAction(style: .destructive, title: DraftPocketStrings(self.presentationData.strings).delete) { [weak self] _, _, done in
            guard let self, !self.busy else { done(false); return }
            if indexPath.section == 0 { self.deleteDraft?(id) } else { self.finalizeDeleted?(id) }
            done(true)
        }
        let configuration = UISwipeActionsConfiguration(actions: [action])
        configuration.performsFirstActionWithFullSwipe = false
        return configuration
    }
}

private func draftPocketContainsMedia(_ content: ChatInputContent) -> Bool {
    return !content.allMedia.isEmpty
}
