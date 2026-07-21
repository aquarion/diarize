// swift/Sources/DiarizeApp/CLIInstaller.swift
import AppKit
import Foundation

/// Symlinks the `diarize` CLI bundled inside this app (see
/// scripts/build-app.sh) onto PATH, mirroring how VS Code/Docker Desktop
/// offer a "install command line tool" action for their embedded CLIs.
extension AppState {
    private static let cliInstallDestination = "/usr/local/bin/diarize"

    public func installCLI() {
        guard let resourceURL = Bundle.main.resourceURL else {
            presentAlert(title: "Install Failed", message: "Could not locate app resources.")
            return
        }
        let cliSource = resourceURL.appendingPathComponent("diarize")
        guard FileManager.default.fileExists(atPath: cliSource.path) else {
            presentAlert(
                title: "Install Failed",
                message: "The diarize CLI wasn't found inside this app bundle. "
                + "Rebuild with scripts/build-app.sh, which embeds it."
            )
            return
        }

        let destination = Self.cliInstallDestination
        if Self.trySymlink(from: cliSource.path, to: destination) {
            presentAlert(
                title: "CLI Installed",
                message: "You can now run \"diarize\" from Terminal."
            )
            return
        }

        // Direct symlink failed, most likely a permissions issue - retry via
        // an admin-privileged shell command, which shows the native macOS
        // password prompt rather than failing silently.
        let script = """
        do shell script "mkdir -p /usr/local/bin && ln -sf '\(cliSource.path)' '\(destination)'" \
        with administrator privileges
        """
        if let appleScript = NSAppleScript(source: script) {
            var errorDict: NSDictionary?
            appleScript.executeAndReturnError(&errorDict)
            if errorDict == nil {
                presentAlert(
                    title: "CLI Installed",
                    message: "You can now run \"diarize\" from Terminal."
                )
                return
            }
        }

        presentAlert(
            title: "Install Failed",
            message: "Could not create \(destination). You can symlink it yourself:\n"
            + "ln -sf \"\(cliSource.path)\" \(destination)"
        )
    }

    private static func trySymlink(from source: String, to destination: String) -> Bool {
        let fm = FileManager.default
        if fm.fileExists(atPath: destination) {
            try? fm.removeItem(atPath: destination)
        }
        return (try? fm.createSymbolicLink(atPath: destination, withDestinationPath: source)) != nil
    }

    private func presentAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.runModal()
    }
}
