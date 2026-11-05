import Foundation
import Darwin
import SwiftSignalKit
import XCTest
@testable import Postbox

/// Real bytes, real MediaBox eviction APIs and an independently reopened retention store.
/// These tests must run in the native simulator target; they are not codec-only substitutes.
final class MediaBoxDraftRetentionTests: XCTestCase {
    private struct ValidationChild: Codable, Equatable {
        let number: Int64
        let labels: [String]
    }

    private struct ValidationRecord: Codable, Equatable {
        let version: Int32
        let child: ValidationChild
        let children: [ValidationChild]
        let bytes: [Data]
        let numbers: [Int32]
    }

    func testOptInPostboxValidationAcceptsRealNestedCodecOutput() throws {
        let child = ValidationChild(number: 42, labels: ["עברית", "nested"])
        let value = ValidationRecord(version: 1, child: child, children: [child], bytes: [Data([0xff, 0, 1]), Data()], numbers: [1, 2])
        let encoded = try AdaptedPostboxEncoder().encode(value)
        XCTAssertTrue(PostboxDecoder.isWellFormedData(encoded))
        XCTAssertEqual(try AdaptedPostboxDecoder(validateData: true).decode(ValidationRecord.self, from: encoded), value)
        XCTAssertEqual(try AdaptedPostboxDecoder().decode(ValidationRecord.self, from: encoded), value)
    }

    func testOptInPostboxValidationAcceptsEveryExistingWireKind() {
        let encoder = PostboxEncoder()
        encoder.encodeInt32(1, forKey: "int32")
        encoder.encodeInt64(2, forKey: "int64")
        encoder.encodeBool(true, forKey: "bool")
        encoder.encodeDouble(1.5, forKey: "double")
        encoder.encodeString("text", forKey: "string")
        encoder.encodeObjectWithEncoder(Int32(3), encoder: { $0.encodeInt32(3, forKey: "value") }, forKey: "object")
        encoder.encodeInt32Array([1, 2], forKey: "int32Array")
        encoder.encodeInt64Array([3, 4], forKey: "int64Array")
        encoder.encodeObjectArrayWithEncoder([Int32(5)], forKey: "objectArray", encoder: { $1.encodeInt32($0, forKey: "value") })
        encoder.encodeObjectDictionary([MediaId(namespace: 1, id: 2): MediaId(namespace: 3, id: 4)], forKey: "dictionary")
        encoder.encodeData(Data([0xff]), forKey: "bytes")
        encoder.encodeNil(forKey: "nil")
        encoder.encodeStringArray(["one", "two"], forKey: "strings")
        encoder.encodeDataArray([Data([0xff])], forKey: "byteArrays")
        XCTAssertTrue(PostboxDecoder.isWellFormedData(encoder.makeData()))
    }

    func testOptInPostboxValidationRejectsMalformedFramingBeforeLegacyDecoder() throws {
        let invalid: [Data] = [
            Data([0xff, 0, 0x7f]), // actual native regression: negative key length
            Data([3, 0x61]), // truncated key
            Data([1, 0x61, 0xff]), // unknown value type
            Data([1, 0x61, 0, 1]), // truncated Int32
            Data([1, 0x61, 10, 0xff, 0xff, 0xff, 0xff]), // negative byte length
            Data([1, 0x61, 8, 0xff, 0xff, 0xff, 0xff]), // negative object count
            Data([1, 0x61, 5, 0, 0, 0, 0, 3, 0, 0, 0, 0xff, 0, 0]), // malformed nested object
            Data([1, 0x61, 8, 1, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff, 0xff, 0x7f]) // oversized element
        ]
        for data in invalid {
            XCTAssertFalse(PostboxDecoder.isWellFormedData(data))
            XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(ValidationRecord.self, from: data))
        }
        let encoded = try AdaptedPostboxEncoder().encode(ValidationChild(number: 7, labels: ["tail"]))
        for count in 0 ..< encoded.count {
            XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(ValidationChild.self, from: Data(encoded.prefix(count))))
        }
    }

    func testOptInPostboxValidationRejectsWrongContainerAndElementTypes() throws {
        struct Children: Decodable { let children: [ValidationChild] }
        struct Child: Decodable { let child: ValidationChild }
        let objectInsteadOfArray = PostboxEncoder()
        objectInsteadOfArray.encodeObjectWithEncoder(Int32(0), encoder: { _ in }, forKey: "children")
        let primitiveInsteadOfObject = PostboxEncoder()
        primitiveInsteadOfObject.encodeInt32Array([42], forKey: "children")
        for data in [objectInsteadOfArray.makeData(), primitiveInsteadOfObject.makeData()] {
            XCTAssertTrue(PostboxDecoder.isWellFormedData(data), "wire framing is valid; its schema is not")
            XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(Children.self, from: data))
        }
        let arrayInsteadOfObject = PostboxEncoder()
        arrayInsteadOfObject.encodeInt32Array([42], forKey: "child")
        XCTAssertThrowsError(try AdaptedPostboxDecoder(validateData: true).decode(Child.self, from: arrayInsteadOfObject.makeData()))
    }

    func testOptInPostboxValidationBoundsNesting() {
        var data = Data()
        for _ in 0 ... PostboxDataValidation.maximumDepth {
            let encoder = PostboxEncoder()
            encoder.encodeInnerObjectDataWithHeader(typeHash: 0, data: data, valueType: .Object, forKey: "nested")
            data = encoder.makeData()
        }
        XCTAssertFalse(PostboxDecoder.isWellFormedData(data))
    }

    private var directory: URL!
    private let id = MediaResourceId("local-file-9101")
    private let bytes = Data((0 ..< 100_003).map { UInt8($0 % 251) })

    override func setUpWithError() throws {
        self.directory = FileManager.default.temporaryDirectory.appendingPathComponent("draft-retention-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: self.directory, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: self.directory)
    }

    private var original: String { self.directory.path + "/" + self.id.stringRepresentation }

    private func stage(_ store: MediaBoxResourceRetention) throws -> MediaBoxResourceRetention.Resource {
        try self.bytes.write(to: URL(fileURLWithPath: self.original), options: .atomic)
        let session = try store.beginSession()
        defer { session.finish() }
        return try session.stage(id: self.id, completeSourcePath: self.original)
    }

    private func value<T>(_ signal: Signal<T, NoError>) throws -> T {
        let done = self.expectation(description: "native MediaBox callback")
        let result = Atomic<T?>(value: nil)
        let disposable = (signal |> take(1)).start(next: { _ = result.swap($0) }, completed: { done.fulfill() })
        defer { disposable.dispose() }
        self.wait(for: [done], timeout: 15)
        return try XCTUnwrap(result.with { $0 })
    }

    func testReopenRestoresMissingOriginalFromVerifiedIndependentCopy() throws {
        let resource = try self.stage(MediaBoxResourceRetention(mediaBoxPath: self.directory.path))
        XCTAssertEqual(resource.byteCount, Int64(self.bytes.count))
        XCTAssertEqual(resource.sha256.count, 64)
        XCTAssertEqual(unlink(self.original), 0)
        let reopened = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        let session = try reopened.beginSession()
        try session.recoverOriginals()
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: self.original)), self.bytes)
        XCTAssertEqual(try session.pinnedResources(), [resource])
        session.finish()
    }

    func testCacheMutationCannotChangeArchiveAndStartupRepairsOriginal() throws {
        let store = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        _ = try self.stage(store)
        try Data(repeating: 0xff, count: self.bytes.count).write(to: URL(fileURLWithPath: self.original), options: .atomic)
        let reopened = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        let session = try reopened.beginSession()
        try session.recoverOriginals()
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: self.original)), self.bytes)
        session.finish()
    }

    func testForceEvictionAndMoveDoNotRemoveOwnedOriginalOrAliases() throws {
        let mediaBox = MediaBox(basePath: self.directory.path, isMainProcess: false)
        _ = try self.stage(mediaBox.draftResourceRetention)
        let alias = self.original + ".mov"
        XCTAssertEqual(link(self.original, alias), 0)
        XCTAssertEqual(try self.value(mediaBox.removeCachedResourcesWithResult([self.id], force: true)), [])
        XCTAssertFalse(mediaBox.draftResourceRetention.removeIfUnowned(paths: [alias]))
        let destination = self.directory.appendingPathComponent("moved-copy").path
        mediaBox.moveResourceData(self.id, toTempPath: destination)
        mediaBox.dataQueue.sync {}
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: destination)), self.bytes)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: self.original)), self.bytes)
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: alias)), self.bytes)
    }

    func testFinalOwnerReleaseAllowsNormalEviction() throws {
        let store = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        let resource = try self.stage(store)
        let session = try store.beginSession()
        try session.collect(authoritativeResources: [resource, resource])
        XCTAssertEqual(try session.pinnedResources(), [resource])
        try session.collect(authoritativeResources: [])
        XCTAssertTrue(try session.pinnedResources().isEmpty)
        session.finish()
        XCTAssertTrue(store.removeIfUnowned(paths: [self.original]))
        XCTAssertFalse(FileManager.default.fileExists(atPath: self.original))
    }

    func testAgeAndSizeEvictionCandidatesRespectDurablePins() throws {
        let store = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        _ = try self.stage(store)
        let disposablePath = self.directory.appendingPathComponent("ordinary-cache").path
        try self.bytes.write(to: URL(fileURLWithPath: disposablePath))
        let queue = Queue(name: "DraftRetentionScan")
        queue.sync {
            guard let database = TempScanDatabase(queue: queue, basePath: self.directory.path + "/scan-db") else {
                XCTFail("The real cache scan database must open")
                return
            }
            defer { database.dispose() }
            var remaining = 100
            var seen = Set<FileIdentity>()
            database.begin()
            let aged = scanFiles(at: self.directory.path, olderThan: Int32(Date().timeIntervalSince1970) + 60,
                includeSubdirectories: false, performSizeMapping: true, tempDatabase: database,
                reportMemoryUsageInterval: 100, reportMemoryUsageRemaining: &remaining, seenLinkedInodes: &seen,
                removeFiles: { store.removeIfUnowned(paths: $0) })
            database.commit()
            XCTAssertEqual(aged.unlinkedCount, 1)
            XCTAssertFalse(FileManager.default.fileExists(atPath: disposablePath))
            XCTAssertTrue(FileManager.default.fileExists(atPath: self.original))
            XCTAssertTrue(FileManager.default.createFile(atPath: disposablePath, contents: self.bytes))
            seen = []
            database.begin()
            let _ = scanFiles(at: self.directory.path, olderThan: 0,
                includeSubdirectories: false, performSizeMapping: true, tempDatabase: database,
                reportMemoryUsageInterval: 100, reportMemoryUsageRemaining: &remaining, seenLinkedInodes: &seen,
                removeFiles: { store.removeIfUnowned(paths: $0) })
            database.commit()
            database.topByAccessTime { _, paths in
                let _ = store.removeIfUnowned(paths: paths)
                return true
            }
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: disposablePath))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: self.original)), self.bytes)
    }

    func testRetainedReadsHonorExtensionAndRequestedRangeAfterCacheMutation() throws {
        let mediaBox = MediaBox(basePath: self.directory.path, isMainProcess: false)
        _ = try self.stage(mediaBox.draftResourceRetention)
        try Data(repeating: 0xff, count: self.bytes.count).write(to: URL(fileURLWithPath: self.original), options: .atomic)
        let full = try self.value(mediaBox.resourceData(id: self.id, pathExtension: "mov", attemptSynchronously: true))
        XCTAssertTrue(full.complete)
        XCTAssertTrue(full.path.hasSuffix(".mov"))
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: full.path)), self.bytes)
        let range = try self.value(mediaBox.resourceData(id: self.id, size: Int64(self.bytes.count), in: 17 ..< 1017, attemptSynchronously: false))
        XCTAssertTrue(range.1)
        XCTAssertEqual(range.0, self.bytes.subdata(in: 17 ..< 1017))
    }

    func testAbandonedPrepareIsReclaimedOnlyAfterFreshEmptyOwnerSnapshot() throws {
        let store = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        _ = try self.stage(store) // Simulates crash after pin publication but before SQLite commit.
        XCTAssertFalse(store.removeIfUnowned(paths: [self.original]))
        let session = try MediaBoxResourceRetention(mediaBoxPath: self.directory.path).beginSession()
        try session.collect(authoritativeResources: [])
        session.finish()
        XCTAssertTrue(store.removeIfUnowned(paths: [self.original]))
    }

    func testFailedPrepareDoesNotPublishOwnerProof() throws {
        let store = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        let session = try store.beginSession()
        XCTAssertThrowsError(try session.stage(id: self.id, completeSourcePath: self.original))
        XCTAssertTrue(try session.pinnedResources().isEmpty)
        XCTAssertThrowsError(try session.stage(id: MediaResourceId("../outside"), completeSourcePath: self.original))
        session.finish()
    }

    func testSeparateStoreCannotEvictWhilePreparationLockIsHeld() throws {
        let first = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        try self.bytes.write(to: URL(fileURLWithPath: self.original))
        let session = try first.beginSession()
        let second = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        XCTAssertFalse(second.removeIfUnowned(paths: [self.original], wait: false))
        let resource = try session.stage(id: self.id, completeSourcePath: self.original)
        session.finish()
        XCTAssertFalse(second.removeIfUnowned(paths: [self.original], wait: false))
        let cleanup = try second.beginSession()
        try cleanup.collect(authoritativeResources: [resource])
        cleanup.finish()
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: self.original)), self.bytes)
    }

    func testSymlinkedArchiveDirectoryCannotDeleteExternalBytes() throws {
        let store = MediaBoxResourceRetention(mediaBoxPath: self.directory.path)
        let session = try store.beginSession()
        let outside = self.directory.appendingPathComponent("external")
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        let outsideFile = outside.appendingPathComponent("keep")
        try self.bytes.write(to: outsideFile)
        let malicious = self.directory.path + "/retained-draft-resources/" + String(repeating: "a", count: 64)
        XCTAssertEqual(symlink(outside.path, malicious), 0)
        XCTAssertThrowsError(try session.collect(authoritativeResources: []))
        XCTAssertEqual(try Data(contentsOf: outsideFile), self.bytes)
        session.finish()
    }
}
