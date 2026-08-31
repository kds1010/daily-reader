import ApplicationServices
import CoreGraphics

struct AXElementSnapshot: Identifiable {
    let id: String
    let element: AXUIElement
    let frame: CGRect
    let role: String
    let title: String?
}

struct HintTarget {
    let snapshot: AXElementSnapshot
    let code: String
}

enum HintCodeGenerator {
    static let alphabet = Array("asdfjklghqwertyuiopzxcvbnm")

    static func codes(count: Int) -> [String] {
        guard count > 0 else { return [] }
        var result: [String] = []
        var width = 1
        while result.count < count {
            let total = Int(pow(Double(alphabet.count), Double(width)))
            for value in 0..<total where result.count < count {
                var n = value
                var code = ""
                for _ in 0..<width {
                    code.append(alphabet[n % alphabet.count])
                    n /= alphabet.count
                }
                result.append(String(code.reversed()))
            }
            width += 1
        }
        return result
    }
}
