// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "Diarize",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "DiarizeKit", targets: ["DiarizeKit"]),
        .executable(name: "diarize", targets: ["DiarizeCLI"]),
        .executable(name: "DiarizeApp", targets: ["DiarizeApp"]),
    ],
    dependencies: [
        .package(url: "https://github.com/argmaxinc/argmax-oss-swift.git", branch: "main"),
        .package(url: "https://github.com/apple/swift-argument-parser.git", from: "1.3.0"),
        .package(url: "https://github.com/jamesrochabrun/SwiftAnthropic.git", from: "1.0.0"),
    ],
    targets: [
        .target(
            name: "DiarizeKit",
            dependencies: [
                .product(name: "WhisperKit", package: "argmax-oss-swift"),
                .product(name: "SpeakerKit", package: "argmax-oss-swift"),
                .product(name: "SwiftAnthropic", package: "SwiftAnthropic"),
            ]
        ),
        .executableTarget(
            name: "DiarizeCLI",
            dependencies: [
                "DiarizeKit",
                .product(name: "ArgumentParser", package: "swift-argument-parser"),
            ]
        ),
        .executableTarget(
            name: "DiarizeApp",
            dependencies: [
                "DiarizeKit",
            ]
        ),
        .testTarget(
            name: "DiarizeKitTests",
            dependencies: ["DiarizeKit"]
        ),
    ]
)
