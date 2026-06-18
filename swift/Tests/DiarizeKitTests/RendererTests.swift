import XCTest
@testable import DiarizeKit

final class RendererTests: XCTestCase {
    func testFormatTimestamp() {
        XCTAssertEqual(Renderer.formatTimestamp(0), "[00:00:00]")
        XCTAssertEqual(Renderer.formatTimestamp(3661), "[01:01:01]")
        XCTAssertEqual(Renderer.formatTimestamp(-1), "[00:00:00]")
    }

    func testSRTTimestamp() {
        XCTAssertEqual(Renderer.srtTimestamp(1.5), "00:00:01,500")
        XCTAssertEqual(Renderer.srtTimestamp(3661.123), "01:01:01,123")
    }

    func testVTTTimestamp() {
        XCTAssertEqual(Renderer.vttTimestamp(1.5), "00:00:01.500")
    }

    func testRenderMarkdown() {
        let blocks = [
            Block(speaker: "Alice", start: 0, text: "Hello world."),
            Block(speaker: "Bob", start: 5, text: "Hi there."),
        ]
        let md = Renderer.renderMarkdown(blocks: blocks, title: "Test", audioPath: "/tmp/audio.wav")
        XCTAssertTrue(md.contains("# Test"))
        XCTAssertTrue(md.contains("**Alice** [00:00:00]"))
        XCTAssertTrue(md.contains("Hello world."))
        XCTAssertTrue(md.contains("**Bob** [00:00:05]"))
        XCTAssertTrue(md.hasSuffix("\n"))
    }

    func testRenderSRT() {
        let segs = [
            Segment(start: 0, end: 2, text: "Hello.", speaker: "SPEAKER_00"),
        ]
        let srt = Renderer.renderSRT(segments: segs)
        XCTAssertTrue(srt.contains("1\n"))
        XCTAssertTrue(srt.contains("[SPEAKER_00] Hello."))
        XCTAssertTrue(srt.contains("-->"))
        XCTAssertTrue(srt.hasSuffix("\n"))
    }

    func testRenderVTT() {
        let segs = [Segment(start: 1, end: 3, text: "Test.", speaker: "SPEAKER_01")]
        let vtt = Renderer.renderVTT(segments: segs)
        XCTAssertTrue(vtt.hasPrefix("WEBVTT\n"))
        XCTAssertTrue(vtt.contains("[SPEAKER_01] Test."))
    }

    func testRenderTXT() {
        let segs = [
            Segment(start: 0, end: 1, text: "Line one.", speaker: "A"),
            Segment(start: 1, end: 2, text: "Line two.", speaker: "B"),
        ]
        XCTAssertEqual(Renderer.renderTXT(segments: segs), "Line one.\nLine two.\n")
    }
}
