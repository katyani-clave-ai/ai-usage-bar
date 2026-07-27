import AppKit

// usage-bar — native macOS menu-bar indicator for Codex + Claude usage limits.
// This Swift shell just renders the output of the Python "brain" (which does all
// the data reading + notifications) into the menu bar and a dropdown.

let brainPath = ("~/.local/share/usage-bar/usage_brain.py" as NSString).expandingTildeInPath

// Split "text | color=#hex size=11 ..." into (text, params).
func parseLine(_ line: String) -> (String, [String: String]) {
    guard let r = line.range(of: "|") else { return (line, [:]) }
    let text = String(line[..<r.lowerBound])
    var params: [String: String] = [:]
    for tok in line[r.upperBound...].split(separator: " ") {
        if let eq = tok.range(of: "=") {
            params[String(tok[..<eq.lowerBound])] = String(tok[eq.upperBound...])
        }
    }
    // trim trailing whitespace only (leading indentation is meaningful)
    return (String(text.reversed().drop(while: { $0 == " " }).reversed()), params)
}

func nsColor(_ hex: String?) -> NSColor? {
    guard var s = hex else { return nil }
    if s.hasPrefix("#") { s.removeFirst() }
    guard s.count == 6, let v = Int(s, radix: 16) else { return nil }
    return NSColor(red: CGFloat((v >> 16) & 0xff) / 255.0,
                   green: CGFloat((v >> 8) & 0xff) / 255.0,
                   blue: CGFloat(v & 0xff) / 255.0, alpha: 1.0)
}

final class Indicator: NSObject, NSApplicationDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var timer: Timer?

    func applicationDidFinishLaunching(_ note: Notification) {
        item.button?.title = "Usage…"
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    func refresh() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let out = self?.runBrain() ?? ""
            let lines = out.components(separatedBy: "\n")
            DispatchQueue.main.async { self?.render(lines) }
        }
    }

    func render(_ lines: [String]) {
        let nonEmpty = lines.filter { !$0.isEmpty }
        guard let first = nonEmpty.first else {
            item.button?.title = "Usage ?"
            return
        }
        let (titleText, titleParams) = parseLine(first)
        item.button?.attributedTitle = menuBarTitle(titleText, titleParams["dots"])

        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.appearance = NSAppearance(named: .aqua)   // light dropdown, even in Dark Mode
        for line in lines.dropFirst() {
            if line == "---" { menu.addItem(.separator()); continue }
            if line.isEmpty { continue }
            let (text, params) = parseLine(line)
            if text.trimmingCharacters(in: .whitespaces).isEmpty { continue }

            let isCaption = params["size"] != nil
            let isHeader = !text.hasPrefix(" ") && !isCaption
            let font: NSFont
            let fallback: NSColor
            if isHeader {                       // section header: dark gray, semibold
                font = .systemFont(ofSize: 11, weight: .semibold)
                fallback = NSColor(white: 0.28, alpha: 1)
            } else if isCaption {               // small note: medium gray
                font = .systemFont(ofSize: 11, weight: .regular)
                fallback = NSColor(white: 0.42, alpha: 1)
            } else {                            // data row: near-black, readable
                font = .systemFont(ofSize: 13, weight: .medium)
                fallback = NSColor(white: 0.10, alpha: 1)
            }
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: nsColor(params["color"]) ?? fallback,
            ]
            // Keep items ENABLED (with autoenablesItems=false) so macOS doesn't
            // dim them; action stays nil so a click is a harmless no-op.
            let mstr = NSMutableAttributedString(string: text, attributes: attrs)
            if let rc = nsColor(params["ring"]) {   // meter: filled cells tinted, empty cells muted
                var loc = 0
                for ch in text {
                    let len = String(ch).utf16.count
                    if ch == "▰" {
                        mstr.addAttribute(.foregroundColor, value: rc, range: NSRange(location: loc, length: len))
                    } else if ch == "▱" {
                        mstr.addAttribute(.foregroundColor, value: rc.withAlphaComponent(0.28), range: NSRange(location: loc, length: len))
                    }
                    loc += len
                }
            }
            let mi = NSMenuItem(title: text, action: nil, keyEquivalent: "")
            mi.attributedTitle = mstr
            menu.addItem(mi)
        }
        menu.addItem(.separator())
        let info = NSMenuItem(title: "ⓘ  What do these mean?", action: #selector(showInfo), keyEquivalent: "")
        info.target = self; menu.addItem(info)
        let r = NSMenuItem(title: "Refresh now", action: #selector(refreshNow), keyEquivalent: "r")
        r.target = self; menu.addItem(r)
        let q = NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q")
        q.target = self; menu.addItem(q)
        item.menu = menu
    }

    // Build the menu-bar title: neutral adaptive text, with each "●" tinted from
    // the brain's `dots=` param and shrunk a touch so it reads as a small dot.
    func menuBarTitle(_ text: String, _ dots: String?) -> NSAttributedString {
        let attr = NSMutableAttributedString(string: text)
        attr.addAttributes(
            [.font: NSFont.systemFont(ofSize: 13, weight: .medium),
             .foregroundColor: NSColor.labelColor],
            range: NSRange(location: 0, length: attr.length))
        guard let dots = dots else { return attr }
        let colors = dots.split(separator: ",").map(String.init)
        let ns = text as NSString
        var start = 0, idx = 0
        while start < ns.length {
            let r = ns.range(of: "●", range: NSRange(location: start, length: ns.length - start))
            if r.location == NSNotFound { break }
            if idx < colors.count, let c = nsColor(colors[idx]) {
                attr.addAttributes([.foregroundColor: c,
                                    .font: NSFont.systemFont(ofSize: 9),
                                    .baselineOffset: 1.0], range: r)
            }
            idx += 1
            start = r.location + r.length
        }
        return attr
    }

    @objc func refreshNow() { refresh() }
    @objc func quit() { NSApp.terminate(nil) }

    @objc func showInfo() {
        let a = NSAlert()
        a.alertStyle = .informational
        a.messageText = "How your usage is calculated"
        a.informativeText = """
        Numbers show how much you have LEFT (battery-style): green = plenty, \
        red = almost out. ~ marks an estimate.

        CODEX
        • Weekly — exact, reported by Codex. One shared pool across all your models.
        • 5h ~ — estimate. Codex no longer reports a 5h window, so this is \
        rebuilt from your rollout token logs: burn in the trailing 5 hours vs \
        your busiest 5 hours.

        CLAUDE
        • 5h ~ — estimate from ccusage: this 5h block vs your peak 5h block. \
        "You usually use ~X%" is your 7-day average per block, for context.

        ALERTS
        Only the enforced limits notify (Codex windows + Claude 5h). Warning at \
        20% left, critical at 10% left. Estimates are shown but never alarm.
        """
        a.addButton(withTitle: "Got it")
        NSApp.activate(ignoringOtherApps: true)
        a.runModal()
    }

    func runBrain() -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.arguments = ["python3", brainPath]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = Pipe()
        do { try p.run() } catch { return "Usage err |" }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return String(data: data, encoding: .utf8) ?? ""
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)   // menu-bar only, no Dock icon
let delegate = Indicator()
app.delegate = delegate
app.run()
