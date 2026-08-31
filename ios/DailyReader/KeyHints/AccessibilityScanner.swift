import AppKit
import ApplicationServices

final class AccessibilityScanner {
    private let queue = DispatchQueue(label: "net.skmin.keyhints.ax", qos: .userInitiated)

    func scan(completion: @escaping ([AXElementSnapshot], NSRunningApplication?) -> Void) {
        queue.async {
            let app = NSWorkspace.shared.frontmostApplication
            guard let app, app.processIdentifier != ProcessInfo.processInfo.processIdentifier else {
                DispatchQueue.main.async { completion([], app) }; return
            }
            let root = AXUIElementCreateApplication(app.processIdentifier)
            AXUIElementSetMessagingTimeout(root, 0.15)
            var windows: CFTypeRef?
            let error = AXUIElementCopyAttributeValue(root, kAXWindowsAttribute as CFString, &windows)
            let list = (windows as? [AXUIElement]) ?? (error == .success ? [] : [])
            var snapshots: [AXElementSnapshot] = []
            var stack = list
            var seen = Set<CFHashCode>()
            while let element = stack.popLast(), snapshots.count < 10_000 {
                let hash = CFHash(element)
                guard seen.insert(hash).inserted else { continue }
                func attribute(_ key: CFString) -> CFTypeRef? {
                    var value: CFTypeRef?
                    _ = AXUIElementCopyAttributeValue(element, key, &value)
                    return value
                }
                let role = attribute(kAXRoleAttribute as CFString) as? String ?? ""
                let title = attribute(kAXTitleAttribute as CFString) as? String
                var frame = CGRect.zero
                if let position = attribute(kAXPositionAttribute as CFString), let size = attribute(kAXSizeAttribute as CFString) {
                    var point = CGPoint.zero
                    var dimensions = CGSize.zero
                    AXValueGetValue(position as! AXValue, .cgPoint, &point)
                    AXValueGetValue(size as! AXValue, .cgSize, &dimensions)
                    frame = CGRect(origin: point, size: dimensions)
                }
                if frame.width > 1, frame.height > 1, frame.intersects(NSScreen.screens.reduce(CGRect.null) { $0.union($1.frame) }),
                   ["AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXMenuItem", "AXPopUpButton", "AXTextField", "AXComboBox", "AXSlider", "AXIncrementor"].contains(role) {
                    snapshots.append(AXElementSnapshot(id: "\(hash)", element: element, frame: frame, role: role, title: title))
                }
                if let children = attribute(kAXChildrenAttribute as CFString) as? [AXUIElement] { stack.append(contentsOf: children) }
            }
            snapshots.sort { $0.frame.minY == $1.frame.minY ? $0.frame.minX < $1.frame.minX : $0.frame.minY < $1.frame.minY }
            DispatchQueue.main.async { completion(snapshots, app) }
        }
    }
}
