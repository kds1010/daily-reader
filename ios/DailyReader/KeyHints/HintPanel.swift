import AppKit

final class HintPanel: NSPanel {
    let label = NSTextField(labelWithString: "")
    init(target: HintTarget, frame: CGRect) {
        super.init(contentRect: frame, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        isOpaque = false; backgroundColor = .clear; level = .screenSaver; ignoresMouseEvents = true; hasShadow = true
        label.stringValue = target.code.uppercased(); label.font = .monospacedSystemFont(ofSize: 12, weight: .bold)
        label.textColor = .white; label.alignment = .center; label.drawsBackground = true; label.backgroundColor = .systemBlue
        label.wantsLayer = true; label.layer?.cornerRadius = 5
        label.frame = NSRect(origin: .zero, size: frame.size); contentView?.addSubview(label)
    }
    override var canBecomeKey: Bool { true }
}
