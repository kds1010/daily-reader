import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

struct MarkdownContentView: View {
    let source: String
    var collapsible = false
    @State private var document = MarkdownDocument("")
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            MarkdownBlocksView(blocks: collapsible && !expanded ? document.preview : document.blocks)
            if collapsible && document.canExpand {
                Button(expanded ? "結果を折りたたむ" : "結果を全文表示") { expanded.toggle() }
                    .appFont(.caption, weight: .semibold)
                    .buttonStyle(.borderless)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .textSelection(.enabled)
        .environment(\.openURL, OpenURLAction { url in
            MarkdownDocument.isSafeLink(url) ? .systemAction : .discarded
        })
        .contextMenu {
            Button("Markdown原文をコピー", systemImage: "doc.on.doc") {
                #if os(macOS)
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(source, forType: .string)
                #else
                UIPasteboard.general.string = source
                #endif
            }
        }
        // Polling unchanged tasks does not reparse or reset the expansion state.
        .onChange(of: source, initial: true) { _, value in
            document = MarkdownDocument(value)
        }
    }
}

private struct MarkdownBlocksView: View {
    let blocks: [MarkdownBlock]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ForEach(blocks) { block in MarkdownBlockView(block: block) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MarkdownBlockView: View {
    @Environment(\.appContentScale) private var scale
    let block: MarkdownBlock

    var body: some View {
        switch block.kind {
        case .header(let level):
            inline(block.text, style: level == 1 ? .title2 : level == 2 ? .title3 : .headline)
                .fontWeight(.bold)
                .accessibilityAddTraits(.isHeader)
        case .codeBlock(let language):
            VStack(alignment: .leading, spacing: 5) {
                if let language, !language.isEmpty {
                    Text(verbatim: language).appFont(.caption).foregroundStyle(.secondary)
                }
                ScrollView(.horizontal) {
                    Text(block.text)
                        .font(AppTypography.font(for: .callout, scale: scale).monospaced())
                        .fixedSize(horizontal: true, vertical: true)
                        .padding(10)
                }
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }
        case .orderedList:
            list(ordered: true)
        case .unorderedList:
            list(ordered: false)
        case .blockQuote:
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 2).fill(.secondary.opacity(0.4)).frame(width: 3)
                MarkdownBlocksView(blocks: block.children)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .table(let columns):
            table(columns: columns)
        case .thematicBreak:
            Divider()
        default:
            if !block.text.characters.isEmpty { inline(block.text) }
            if !block.children.isEmpty { MarkdownBlocksView(blocks: block.children) }
        }
    }

    private func inline(_ text: AttributedString, style: Font.TextStyle = .body) -> some View {
        var styled = text
        for run in text.runs where run.inlinePresentationIntent?.contains(.code) == true {
            styled[run.range].font = AppTypography.font(for: style, scale: scale).monospaced()
        }
        return Text(styled)
            .appFont(style)
            .multilineTextAlignment(.leading)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func list(ordered: Bool) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(block.children) { item in
                HStack(alignment: .top, spacing: 8) {
                    Text(verbatim: marker(item, ordered: ordered)).appFont(.body)
                        .frame(minWidth: 20, alignment: .trailing)
                    MarkdownBlocksView(blocks: item.children)
                }
            }
        }
    }

    private func marker(_ item: MarkdownBlock, ordered: Bool) -> String {
        if ordered, case .listItem(let ordinal) = item.kind { return "\(ordinal)." }
        return "•"
    }

    private func table(columns: [PresentationIntent.TableColumn]) -> some View {
        ScrollView(.horizontal) {
            Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                ForEach(block.children) { row in
                    GridRow(alignment: .top) {
                        ForEach(columns.indices, id: \.self) { index in
                            // Empty cells have no attributed run; preserve their column.
                            let cell = row.children.first { $0.kind == .tableCell(columnIndex: index) }
                            inline(cell?.text ?? AttributedString(""))
                                .fontWeight(row.kind == .tableHeaderRow ? .semibold : .regular)
                                .frame(minWidth: 80, maxWidth: 280, alignment: alignment(columns[index]))
                                .padding(8)
                                .frame(maxHeight: .infinity, alignment: .topLeading)
                                .background(row.kind == .tableHeaderRow ? Color.secondary.opacity(0.12) : .clear)
                                .overlay(Rectangle().stroke(.secondary.opacity(0.25), lineWidth: 0.5))
                        }
                    }
                }
            }
            .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityLabel("表。横スクロールできます")
    }

    private func alignment(_ column: PresentationIntent.TableColumn) -> Alignment {
        switch column.alignment {
        case .center: .top
        case .right: .topTrailing
        default: .topLeading
        }
    }
}
