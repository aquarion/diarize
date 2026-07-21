// swift/Sources/DiarizeCLI/ConfigCommand.swift
import ArgumentParser
import DiarizeKit
import Foundation

struct ConfigCommand: AsyncParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "config",
        abstract: "View or edit configuration.",
        subcommands: [Show.self, Path.self, Get.self, Set.self]
    )

    struct Show: AsyncParsableCommand {
        static let configuration = CommandConfiguration(
            commandName: "show", abstract: "Print the effective config (secrets masked)."
        )
        @Option(name: .long, help: "Path to config JSON file") var config: String?

        mutating func run() async throws {
            let configURL = config.map { URL(fileURLWithPath: $0) } ?? ConfigLoader.configURL
            let (_, raw) = try ConfigLoader.load(from: configURL)
            print("Config file: \(configURL.path)")
            let data = try JSONSerialization.data(
                withJSONObject: ConfigLoader.maskSecrets(raw), options: [.prettyPrinted, .sortedKeys]
            )
            print(String(data: data, encoding: .utf8) ?? "{}")
        }
    }

    struct Path: AsyncParsableCommand {
        static let configuration = CommandConfiguration(
            commandName: "path", abstract: "Print the config file path."
        )
        @Option(name: .long, help: "Path to config JSON file") var config: String?

        mutating func run() async throws {
            let configURL = config.map { URL(fileURLWithPath: $0) } ?? ConfigLoader.configURL
            print(configURL.path)
        }
    }

    struct Get: AsyncParsableCommand {
        static let configuration = CommandConfiguration(
            commandName: "get", abstract: "Print one config value."
        )
        @Argument(help: "Config key, e.g. language") var key: String
        @Option(name: .long, help: "Path to config JSON file") var config: String?

        mutating func run() async throws {
            guard AppConfig.validKeys.contains(key) else {
                throw unknownKeyError(key)
            }
            let configURL = config.map { URL(fileURLWithPath: $0) } ?? ConfigLoader.configURL
            // ConfigLoader.load seeds `raw` from jsonDefaults before merging
            // repo/user config on top, so a known key is always present here.
            let (_, raw) = try ConfigLoader.load(from: configURL)
            print(raw[key] as? String ?? "")
        }
    }

    struct Set: AsyncParsableCommand {
        static let configuration = CommandConfiguration(
            commandName: "set", abstract: "Set one config value and save."
        )
        @Argument(help: "Config key, e.g. language") var key: String
        @Argument(help: "New value") var value: String
        @Option(name: .long, help: "Path to config JSON file") var config: String?

        mutating func run() async throws {
            guard AppConfig.validKeys.contains(key) else {
                throw unknownKeyError(key)
            }
            let configURL = config.map { URL(fileURLWithPath: $0) } ?? ConfigLoader.configURL
            var (_, raw) = try ConfigLoader.load(from: configURL)
            raw[key] = value
            try ConfigLoader.save(raw, to: configURL)
            print("==> Set \(key) = \(ConfigLoader.maskSecret(key: key, value: value)) in \(configURL.path)")
        }
    }
}

private func unknownKeyError(_ key: String) -> Error {
    let keyList = AppConfig.validKeys.sorted().joined(separator: ", ")
    fputs("!! Unknown config key: \(key)\n    Valid keys: \(keyList)\n", stderr)
    return ExitCode(2)
}
