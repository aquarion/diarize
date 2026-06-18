import XCTest
@testable import DiarizeKit

final class SpeakerMapperTests: XCTestCase {
    func testAssignSpeakersMaxOverlap() {
        let segments = [Segment(start: 1.0, end: 3.0, text: "Hello", speaker: "UNKNOWN")]
        let turns = [
            Turn(start: 0.0, end: 2.0, speaker: "SPEAKER_00"),
            Turn(start: 1.5, end: 4.0, speaker: "SPEAKER_01"),
        ]
        // SPEAKER_00 overlaps [1.0, 2.0] = 1.0s; SPEAKER_01 overlaps [1.5, 3.0] = 1.5s
        let result = SpeakerMapper.assignSpeakers(to: segments, from: turns)
        XCTAssertEqual(result[0].speaker, "SPEAKER_01")
    }

    func testAssignSpeakersNoOverlapKeepsUnknown() {
        let segments = [Segment(start: 10.0, end: 12.0, text: "Hello", speaker: "UNKNOWN")]
        let turns = [Turn(start: 0.0, end: 5.0, speaker: "SPEAKER_00")]
        let result = SpeakerMapper.assignSpeakers(to: segments, from: turns)
        XCTAssertEqual(result[0].speaker, "UNKNOWN")
    }

    func testCoalesceConsecutiveSameSpeaker() {
        let segments = [
            Segment(start: 0, end: 1, text: "Hello", speaker: "SPEAKER_00"),
            Segment(start: 1, end: 2, text: "world.", speaker: "SPEAKER_00"),
            Segment(start: 2, end: 3, text: "Bye.", speaker: "SPEAKER_01"),
        ]
        let mapping = ["SPEAKER_00": "Alice", "SPEAKER_01": "Bob"]
        let blocks = SpeakerMapper.coalesce(segments: segments, mapping: mapping)
        XCTAssertEqual(blocks.count, 2)
        XCTAssertEqual(blocks[0].speaker, "Alice")
        XCTAssertEqual(blocks[0].text, "Hello world.")
        XCTAssertEqual(blocks[1].speaker, "Bob")
    }

    func testCoalesceSkipsEmptySegments() {
        let segments = [
            Segment(start: 0, end: 1, text: "  ", speaker: "SPEAKER_00"),
            Segment(start: 1, end: 2, text: "Hello.", speaker: "SPEAKER_00"),
        ]
        let blocks = SpeakerMapper.coalesce(segments: segments, mapping: [:])
        XCTAssertEqual(blocks.count, 1)
        XCTAssertEqual(blocks[0].text, "Hello.")
    }

    func testSpeakerMappingRoundTrip() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("speakers_test.json")
        defer { try? FileManager.default.removeItem(at: url) }
        let mapping = ["SPEAKER_00": "Alice", "SPEAKER_01": "Bob"]
        try SpeakerMapper.saveMapping(mapping, to: url)
        let loaded = SpeakerMapper.loadMapping(from: url)
        XCTAssertEqual(loaded["SPEAKER_00"], "Alice")
        XCTAssertEqual(loaded["SPEAKER_01"], "Bob")
        XCTAssertNil(loaded["_comment"])
    }
}
