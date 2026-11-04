import Foundation
import CryptoKit
import Darwin

/// Durable byte ownership for account-local drafts. Postbox owns the authoritative
/// owner graph; these immutable files and conservative pins bridge the filesystem/SQLite commit.
/// A session MUST remain strongly held in a transaction's RESULT until SQLite has committed.
/// Never block acquiring a session from inside a Postbox transaction.
public final class MediaBoxResourceRetention {
    public enum Failure: Error {
        case invalidResourceId
        case io(Int32)
        case incompleteSource
        case resourceChanged
        case corruptArchive
        case unsupportedVersion
    }

    public struct Resource: Codable, Equatable {
        public let id: String
        public let byteCount: Int64
        public let sha256: String
    }

    private struct Pin: Codable {
        var version: Int32 = 1
        var resource: Resource
    }

    public final class Session {
        private let store: MediaBoxResourceRetention
        private var descriptor: Int32

        fileprivate init(store: MediaBoxResourceRetention, descriptor: Int32) {
            self.store = store
            self.descriptor = descriptor
        }

        deinit {
            self.finish()
        }

        /// Release immediately after the commit/recovery work and BEFORE invoking subscribers.
        /// Sessions are used serially; no further operations are permitted after finish().
        public func finish() {
            if self.descriptor >= 0 {
                _ = flock(self.descriptor, LOCK_UN)
                _ = close(self.descriptor)
                self.descriptor = -1
            }
        }

        /// No network fetch or upload is started. Only a stable, complete regular file is accepted.
        /// The archive is a COPY, not a hard link: subsequent cache writes cannot alter draft bytes.
        public func stage(id: MediaResourceId, completeSourcePath: String) throws -> Resource {
            guard self.descriptor >= 0 else { throw Failure.io(EBADF) }
            let id = id.stringRepresentation
            try self.store.validate(id: id)
            let directory = self.store.directory(id: id)
            try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: true)
            try self.store.validateDirectory(directory)
            try self.store.syncDirectory(self.store.root)
            if let pin = try self.store.readPin(id: id) {
                let archive = directory + "/data"
                guard try self.store.digest(path: archive) == pin.resource else { throw Failure.corruptArchive }
                if self.store.regularFileExists(completeSourcePath) {
                    guard try self.store.digest(path: completeSourcePath, id: id) == pin.resource else { throw Failure.resourceChanged }
                }
                try self.store.restoreOriginal(pin.resource)
                return pin.resource
            }

            let staging = directory + "/stage-" + UUID().uuidString
            defer { _ = unlink(staging) }
            let resource = try self.store.copyVerified(from: completeSourcePath, to: staging, id: id)
            let archive = directory + "/data"
            guard rename(staging, archive) == 0 else { throw Failure.io(errno) }
            try self.store.syncDirectory(directory)
            // Pin publication is durable BEFORE the caller is allowed to commit any composer clear.
            try self.store.writePin(Pin(resource: resource), directory: directory)
            try self.store.restoreOriginal(resource)
            return resource
        }

        /// Run while holding this session, using an owner snapshot read in a NEW Postbox transaction
        /// after acquiring the session. A snapshot taken before the lock is unsafe across processes.
        /// Unlinking archive bytes is always AFTER the durable owner deletion commit.
        public func collect(authoritativeResources resources: [Resource]) throws {
            guard self.descriptor >= 0 else { throw Failure.io(EBADF) }
            var owned: [String: Resource] = [:]
            for resource in resources {
                if let previous = owned[resource.id], previous != resource { throw Failure.resourceChanged }
                owned[resource.id] = resource
            }
            let pins = try self.store.pins()
            // Fail closed before deleting anything if the authoritative graph lost an archive.
            for resource in owned.values {
                guard let pin = pins.first(where: { $0.resource.id == resource.id }), pin.resource == resource else { throw Failure.corruptArchive }
                guard self.store.regularFileExists(self.store.directory(id: resource.id) + "/data") else { throw Failure.corruptArchive }
            }
            for pin in pins {
                if let resource = owned[pin.resource.id] {
                    guard resource == pin.resource else { throw Failure.resourceChanged }
                    continue
                }
                let directory = self.store.directory(id: pin.resource.id)
                // Preserve the pin if any archive unlink fails. The ordinary cache remains intact.
                for name in try FileManager.default.contentsOfDirectory(atPath: directory) where name != "pin.json" {
                    guard !name.contains("/"), !name.contains("\\") else { throw Failure.corruptArchive }
                    let path = directory + "/" + name
                    guard unlink(path) == 0 || errno == ENOENT else { throw Failure.io(errno) }
                }
                try self.store.syncDirectory(directory)
                guard unlink(directory + "/pin.json") == 0 || errno == ENOENT else { throw Failure.io(errno) }
                try self.store.syncDirectory(directory)
            }
            // A crash before publishing pin.json may leave a staged copy. No transaction can
            // own it yet; the global lock prevents racing another process's preparation.
            for name in try FileManager.default.contentsOfDirectory(atPath: self.store.root) where name != "lock" {
                guard name.count == 64, name.allSatisfy({ $0.isHexDigit }) else { throw Failure.corruptArchive }
                let directory = self.store.root + "/" + name
                try self.store.validateDirectory(directory)
                guard !FileManager.default.fileExists(atPath: directory + "/pin.json") else { continue }
                for child in try FileManager.default.contentsOfDirectory(atPath: directory) {
                    guard unlink(directory + "/" + child) == 0 || errno == ENOENT else { throw Failure.io(errno) }
                }
                guard rmdir(directory) == 0 || errno == ENOENT else { throw Failure.io(errno) }
            }
            try self.store.syncDirectory(self.store.root)
        }

        /// Startup recovery precedes normal cleanup and pending-message uploads. Existing originals
        /// are checked against the digest; missing originals are restored from verified bytes.
        public func recoverOriginals() throws {
            guard self.descriptor >= 0 else { throw Failure.io(EBADF) }
            for pin in try self.store.pins() {
                try self.store.restoreOriginal(pin.resource)
            }
        }

        /// Recover healthy owned resources even if an unrelated archive is corrupt.
        /// A malformed index still throws because its affected id cannot be trusted.
        public func recoverOriginalsReportingFailures() throws -> Set<String> {
            guard self.descriptor >= 0 else { throw Failure.io(EBADF) }
            var failures = Set<String>()
            for pin in try self.store.pins() {
                do { try self.store.restoreOriginal(pin.resource) }
                catch { failures.insert(pin.resource.id) }
            }
            return failures
        }

        public func pinnedResources() throws -> [Resource] {
            guard self.descriptor >= 0 else { throw Failure.io(EBADF) }
            return try self.store.pins().map(\.resource)
        }
    }

    private let mediaBoxPath: String
    private let root: String

    public init(mediaBoxPath: String) {
        self.mediaBoxPath = URL(fileURLWithPath: mediaBoxPath).standardizedFileURL.path
        self.root = self.mediaBoxPath + "/retained-draft-resources"
    }

    public var hasPersistedResources: Bool {
        return FileManager.default.fileExists(atPath: self.root)
    }

    /// Conservative ownership probe for unrelated-send isolation when metadata is unreadable.
    /// A malformed resource identifier cannot establish that the bytes are unowned.
    public func hasRetainedPin(id: String) -> Bool {
        guard (try? self.validate(id: id)) != nil else { return true }
        return FileManager.default.fileExists(atPath: self.directory(id: id) + "/pin.json")
    }

    public func beginSession() throws -> Session {
        return try self.lock(wait: true)
    }

    /// Read-only alias. Never use this path as a write/fetch destination. Owned resources cannot
    /// be collected; after last-owner release it has ordinary cache lifetime semantics.
    public func retainedCompletePath(id: MediaResourceId) -> String? {
        guard let pin = try? self.readPin(id: id.stringRepresentation) else { return nil }
        let path = self.directory(id: pin.resource.id) + "/data"
        var information = stat()
        guard lstat(path, &information) == 0, information.st_mode & S_IFMT == S_IFREG,
              information.st_size == pin.resource.byteCount else { return nil }
        return path
    }

    private func lock(wait: Bool) throws -> Session {
        let existed = FileManager.default.fileExists(atPath: self.root)
        try FileManager.default.createDirectory(atPath: self.root, withIntermediateDirectories: true)
        try self.validateDirectory(self.mediaBoxPath)
        try self.validateDirectory(self.root)
        if !existed { try self.syncDirectory(self.mediaBoxPath) }
        let descriptor = open(self.root + "/lock", O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else { throw Failure.io(errno) }
        while flock(descriptor, LOCK_EX | (wait ? 0 : LOCK_NB)) != 0 {
            if errno == EINTR { continue }
            let error = errno
            _ = close(descriptor)
            throw Failure.io(error)
        }
        return Session(store: self, descriptor: descriptor)
    }

    /// Every normal eviction goes through the SAME cross-process lock as prepare/commit/recovery.
    /// A Boolean pin check followed by an unlocked unlink has a destructive prepare/evict race.
    /// `wait: false` is for synchronous move callers that may already own a Postbox transaction:
    /// a contended move keeps the source (copy semantics) rather than introducing a lock inversion.
    @discardableResult
    public func removeIfUnowned(paths: [String], wait: Bool = true) -> Bool {
        guard let session = try? self.lock(wait: wait) else { return false }
        return withExtendedLifetime(session) {
            for path in paths {
                guard self.isInsideMediaBox(path), !self.isProtected(path: path) else { return false }
            }
            var success = true
            for path in paths {
                if unlink(path) != 0 && errno != ENOENT { success = false }
            }
            return success
        }
    }

    private func isInsideMediaBox(_ path: String) -> Bool {
        return URL(fileURLWithPath: path).standardizedFileURL.path.hasPrefix(self.mediaBoxPath + "/")
    }

    private func isProtected(path: String) -> Bool {
        let normalized = URL(fileURLWithPath: path).standardizedFileURL.path
        if normalized == self.root || normalized.hasPrefix(self.root + "/") { return true }
        guard (normalized as NSString).deletingLastPathComponent == self.mediaBoxPath else { return false }
        let name = (normalized as NSString).lastPathComponent
        var candidates = [MediaBox.idForFileName(name: name)]
        // completedResourcePath(pathExtension:) creates hard-link aliases next to the original.
        for index in name.indices where name[index] == "." {
            candidates.append(String(name[..<index]))
        }
        return candidates.contains { FileManager.default.fileExists(atPath: self.directory(id: $0) + "/pin.json") }
    }

    private func validate(id: String) throws {
        guard !id.isEmpty, id != ".", id != "..", !id.contains("/"), !id.contains("\\"), !id.contains("\0") else { throw Failure.invalidResourceId }
    }

    private func directory(id: String) -> String {
        let digest = SHA256.hash(data: Data(id.utf8)).map { String(format: "%02x", $0) }.joined()
        return self.root + "/" + digest
    }

    private func readPin(id: String) throws -> Pin? {
        try self.validateDirectory(self.directory(id: id))
        let path = self.directory(id: id) + "/pin.json"
        guard FileManager.default.fileExists(atPath: path) else { return nil }
        guard self.regularFileExists(path) else { throw Failure.corruptArchive }
        let pin = try JSONDecoder().decode(Pin.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        guard pin.version == 1 else { throw Failure.unsupportedVersion }
        guard pin.resource.id == id, pin.resource.byteCount >= 0 else { throw Failure.corruptArchive }
        return pin
    }

    private func pins() throws -> [Pin] {
        var pins: [Pin] = []
        for name in try FileManager.default.contentsOfDirectory(atPath: self.root) where name != "lock" {
            try self.validateDirectory(self.root + "/" + name)
            let path = self.root + "/" + name + "/pin.json"
            guard FileManager.default.fileExists(atPath: path) else { continue }
            guard self.regularFileExists(path) else { throw Failure.corruptArchive }
            let pin = try JSONDecoder().decode(Pin.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
            guard pin.version == 1 else { throw Failure.unsupportedVersion }
            try self.validate(id: pin.resource.id)
            guard self.directory(id: pin.resource.id) == self.root + "/" + name else { throw Failure.corruptArchive }
            pins.append(pin)
        }
        return pins
    }

    private func writePin(_ pin: Pin, directory: String) throws {
        let path = directory + "/pin.json"
        try JSONEncoder().encode(pin).write(to: URL(fileURLWithPath: path), options: .atomic)
        try self.syncFile(path)
        try self.syncDirectory(directory)
    }

    private func regularFileExists(_ path: String) -> Bool {
        var value = stat()
        return lstat(path, &value) == 0 && value.st_mode & S_IFMT == S_IFREG
    }

    private func validateDirectory(_ path: String) throws {
        var information = stat()
        guard lstat(path, &information) == 0, information.st_mode & S_IFMT == S_IFDIR else { throw Failure.corruptArchive }
    }

    private func digest(path: String, id: String? = nil) throws -> Resource {
        let descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw Failure.io(errno) }
        defer { _ = close(descriptor) }
        var information = stat()
        guard fstat(descriptor, &information) == 0, information.st_mode & S_IFMT == S_IFREG else { throw Failure.incompleteSource }
        var hash = SHA256()
        var count: Int64 = 0
        var buffer = [UInt8](repeating: 0, count: 256 * 1024)
        while true {
            let readCount = read(descriptor, &buffer, buffer.count)
            if readCount < 0 { if errno == EINTR { continue }; throw Failure.io(errno) }
            if readCount == 0 { break }
            hash.update(data: Data(buffer.prefix(readCount)))
            count += Int64(readCount)
        }
        var after = stat()
        guard fstat(descriptor, &after) == 0, count == information.st_size,
              information.st_size == after.st_size,
              information.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              information.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec else { throw Failure.resourceChanged }
        // Archive reads recover their id from the pin in the same locked directory.
        let resourceId: String
        if let id = id { resourceId = id } else {
            let pin = try JSONDecoder().decode(Pin.self, from: Data(contentsOf: URL(fileURLWithPath: (path as NSString).deletingLastPathComponent + "/pin.json")))
            resourceId = pin.resource.id
        }
        return Resource(id: resourceId, byteCount: count, sha256: hash.finalize().map { String(format: "%02x", $0) }.joined())
    }

    private func copyVerified(from source: String, to target: String, id: String) throws -> Resource {
        let input = open(source, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard input >= 0 else { throw Failure.io(errno) }
        defer { _ = close(input) }
        var before = stat()
        guard fstat(input, &before) == 0, before.st_mode & S_IFMT == S_IFREG else { throw Failure.incompleteSource }
        let output = open(target, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, S_IRUSR | S_IWUSR)
        guard output >= 0 else { throw Failure.io(errno) }
        defer { _ = close(output) }
        var hash = SHA256()
        var count: Int64 = 0
        var buffer = [UInt8](repeating: 0, count: 256 * 1024)
        while true {
            let readCount = read(input, &buffer, buffer.count)
            if readCount < 0 { if errno == EINTR { continue }; throw Failure.io(errno) }
            if readCount == 0 { break }
            let data = Data(buffer.prefix(readCount))
            hash.update(data: data)
            try data.withUnsafeBytes { bytes in
                var written = 0
                while written < readCount {
                    let result = write(output, bytes.baseAddress!.advanced(by: written), readCount - written)
                    if result < 0 { if errno == EINTR { continue }; throw Failure.io(errno) }
                    guard result > 0 else { throw Failure.io(EIO) }
                    written += result
                }
            }
            count += Int64(readCount)
        }
        var after = stat()
        guard fstat(input, &after) == 0, count == before.st_size, before.st_size == after.st_size,
              before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec,
              before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec else { throw Failure.resourceChanged }
        guard fsync(output) == 0 else { throw Failure.io(errno) }
        let resource = Resource(id: id, byteCount: count, sha256: hash.finalize().map { String(format: "%02x", $0) }.joined())
        guard try self.digest(path: target, id: id) == resource else { throw Failure.corruptArchive }
        return resource
    }

    private func restoreOriginal(_ resource: Resource) throws {
        try self.validate(id: resource.id)
        let original = self.mediaBoxPath + "/" + resource.id
        if self.regularFileExists(original) {
            if try self.digest(path: original, id: resource.id) == resource { return }
        }
        let archive = self.directory(id: resource.id) + "/data"
        guard try self.digest(path: archive, id: resource.id) == resource else { throw Failure.corruptArchive }
        let staging = self.mediaBoxPath + "/retention-restore-" + UUID().uuidString
        defer { _ = unlink(staging) }
        guard try self.copyVerified(from: archive, to: staging, id: resource.id) == resource else { throw Failure.corruptArchive }
        guard rename(staging, original) == 0 else { throw Failure.io(errno) }
        try self.syncDirectory(self.mediaBoxPath)
    }

    private func syncFile(_ path: String) throws {
        let descriptor = open(path, O_RDONLY | O_CLOEXEC)
        guard descriptor >= 0 else { throw Failure.io(errno) }
        defer { _ = close(descriptor) }
        guard fsync(descriptor) == 0 else { throw Failure.io(errno) }
    }

    private func syncDirectory(_ path: String) throws {
        try self.syncFile(path)
    }
}
