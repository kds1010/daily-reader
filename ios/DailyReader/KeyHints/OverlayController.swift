import AppKit

final class OverlayController {
    private var panels: [HintPanel] = []
    func show(_ targets: [HintTarget]) {
        hide()
        panels = targets.map { target in
            let axFrame = target.snapshot.frame
            let screen = NSScreen.screens.first { $0.frame.contains(CGPoint(x: axFrame.midX, y: axFrame.midY)) }
            let converted = CGRect(x: axFrame.minX, y: (screen?.frame.maxY ?? 0) - axFrame.maxY, width: axFrame.width, height: axFrame.height)
            let view = HintPanel(target: target, frame: converted); view.orderFrontRegardless(); return view
        }
    }
    func hide() { panels.forEach { $0.orderOut(nil) }; panels.removeAll() }
}
