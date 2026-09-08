import Foundation

/// Foundation supplies the Markdown grammar; this tree restores block boundaries
/// that would be lost by displaying the parsed AttributedString as one Text.
struct MarkdownBlock: Identifiable, Equatable {
    let id: Int
    let kind: PresentationIntent.Kind
    var text = AttributedString()
    var children: [MarkdownBlock] = []

    var characterCount: Int {
        text.characters.count + children.reduce(0) { $0 + $1.characterCount }
    }
}

struct MarkdownDocument: Equatable {
    let blocks: [MarkdownBlock]

    init(_ source: String) {
        do {
            let parsed = try AttributedString(
                markdown: source,
                options: .init(allowsExtendedAttributes: false, interpretedSyntax: .full)
            )
            let root = Builder(id: -1, kind: .paragraph)
            var nodes: [Int: Builder] = [:]
            var rawID = -2
            for run in parsed.runs {
                var text = AttributedString(parsed[run.range])
                text.presentationIntent = nil
                if let link = text.link, !Self.isSafeLink(link) { text.link = nil }
                // Keep image alt text without fetching remote content.
                if text.imageURL != nil {
                    if String(text.characters) == "\u{FFFC}" { text = AttributedString("画像") }
                    text.imageURL = nil
                }
                if run.inlinePresentationIntent?.contains(.softBreak) == true {
                    text = AttributedString("\n")
                }
                var parent = root
                if let intent = run.presentationIntent, !intent.components.isEmpty {
                    for component in intent.components.reversed() {
                        if let existing = nodes[component.identity] {
                            parent = existing
                        } else {
                            let node = Builder(id: component.identity, kind: component.kind)
                            nodes[component.identity] = node
                            parent.children.append(node)
                            parent = node
                        }
                    }
                } else {
                    let node = Builder(id: rawID, kind: .paragraph)
                    rawID -= 1
                    root.children.append(node)
                    parent = node
                }
                parent.text.append(text)
            }
            let result = root.children.map { $0.block }
            blocks = result.isEmpty && !source.isEmpty
                ? [MarkdownBlock(id: 0, kind: .paragraph, text: AttributedString(source))]
                : result
        } catch {
            blocks = [MarkdownBlock(id: 0, kind: .paragraph, text: AttributedString(source))]
        }
    }

    /// Never split a list, table, code block or inline construct to make a preview.
    /// A single long block remains complete and selectable.
    var preview: [MarkdownBlock] {
        var count = 0
        var visible: [MarkdownBlock] = []
        for block in blocks {
            if !visible.isEmpty && (visible.count >= 3 || count + block.characterCount > 600) { break }
            visible.append(block)
            count += block.characterCount
        }
        return visible
    }

    var canExpand: Bool { preview.count < blocks.count }

    static func isSafeLink(_ url: URL) -> Bool {
        ["http", "https"].contains(url.scheme?.lowercased() ?? "")
            && url.host?.isEmpty == false && url.user == nil && url.password == nil
    }

    private final class Builder {
        let id: Int
        let kind: PresentationIntent.Kind
        var text = AttributedString()
        var children: [Builder] = []

        init(id: Int, kind: PresentationIntent.Kind) {
            self.id = id
            self.kind = kind
        }

        var block: MarkdownBlock {
            var blocks = children.map { $0.block }
            if case .table = kind {
                // Foundation omits runs for entirely empty rows. Restore gaps
                // using the following nonempty row's original position.
                var rows: [MarkdownBlock] = []
                if blocks.first?.kind != .tableHeaderRow {
                    rows.append(MarkdownBlock(id: -1, kind: .tableHeaderRow))
                }
                var nextRow = 1
                for row in blocks {
                    if case .tableRow(let index) = row.kind {
                        while nextRow < index {
                            rows.append(MarkdownBlock(id: -1 - nextRow, kind: .tableRow(rowIndex: nextRow)))
                            nextRow += 1
                        }
                        nextRow = index + 1
                    }
                    rows.append(row)
                }
                blocks = rows
            }
            return MarkdownBlock(id: id, kind: kind, text: text, children: blocks)
        }
    }
}
