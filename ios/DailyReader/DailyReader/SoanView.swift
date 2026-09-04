import SwiftUI

struct SoanWorkspace: Codable, Identifiable, Hashable { var id: String { root }; let documentID: String; let title: String; let root: String; let access: String? }
struct SoanLineStyle: Codable, Hashable {
    let paragraph: Int?; let hl: String?; let pad: Int?; let mark: String?; let sigil: String?; let blank: Bool?
    let bold: Bool?; let italic: Bool?; let underline: Bool?; let strikethrough: Bool?
    let foreground: String?; let background: String?; let fontSizePt: Double?; let alignment: String?
    static let plain = Self(paragraph: nil, hl: nil, pad: nil, mark: nil, sigil: nil, blank: nil, bold: nil, italic: nil, underline: nil, strikethrough: nil, foreground: nil, background: nil, fontSizePt: nil, alignment: nil)
}
struct SoanImage: Codable, Hashable { let line: Int; let placement: String; let source: String?; let caption: String; let width: Int?; let height: Int?; let version: String?; let missing: Bool? }
struct SoanTab: Codable, Identifiable, Hashable { let id: String; let title: String; let content: String; let styles: [SoanLineStyle]?; let images: [SoanImage]? }
struct SoanDocument: Codable { let documentID: String; let title: String; let revisionID: String; let root: String; let tabs: [SoanTab] }
private struct SoanRequest: Encodable { let root: String; var tabID: String? = nil; var content: String? = nil; var instruction: String? = nil; var base: String? = nil }
private struct SoanProposal: Decodable { let content: String; let llm: String }
private struct SoanSegment { let line: Int; let text: String; let style: SoanLineStyle }
private struct SoanBlock: Identifiable {
    let id: Int; let segments: [SoanSegment]
    var text: String { segments.map(\.text).joined() }
    var style: SoanLineStyle { segments.first?.style ?? .plain }
    func contains(line: Int) -> Bool { segments.contains { $0.line == line } }
}

struct SoanView: View {
    @State private var workspaces: [SoanWorkspace] = []
    @State private var document: SoanDocument?
    @State private var selectedTab: SoanTab?
    @State private var draft = ""
    @State private var selectedBlock: SoanBlock?
    @State private var comment = ""
    @State private var showingComment = false
    @State private var showingTextEditor = false
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        List {
            if let document, let selectedTab {
                if document.tabs.count > 1 {
                    Picker("タブ", selection: Binding(get: { selectedTab.id }, set: { id in select(document.tabs.first { $0.id == id }) })) {
                        ForEach(document.tabs) { Text($0.title).tag($0.id) }
                    }
                }
                Section {
                    ForEach(blocks(for: selectedTab)) { block in blockView(block, tab: selectedTab, root: document.root) }
                } footer: { Text("本文をタップすると、そのブロックについてLLMへコメントできます。") }
            } else {
                Section("文書") {
                    if workspaces.isEmpty && !loading { ContentUnavailableView("Soan文書がありません", systemImage: "doc.text", description: Text("Mac miniのSoan接続を確認してください。")) }
                    ForEach(workspaces) { workspace in
                        Button { Task { await open(workspace) } } label: { HStack { Text(workspace.title); Spacer(); Image(systemName: "chevron.right").foregroundStyle(.tertiary) } }.buttonStyle(.plain)
                    }
                }
            }
            if loading { HStack { Spacer(); ProgressView(); Spacer() } }
            if let error { Section { Text(error).foregroundStyle(.orange); Button("再読み込み") { Task { await reload() } } } }
        }
        .navigationTitle(document?.title ?? "資料")
        .toolbar {
            if document != nil { ToolbarItem(placement: .primaryAction) { Menu {
                Button("文字を直接編集", systemImage: "pencil") { showingTextEditor = true }
                Button("文書一覧へ", systemImage: "folder") { document = nil; selectedTab = nil }
            } label: { Image(systemName: "ellipsis.circle") } } }
        }
        .sheet(isPresented: $showingComment) { commentSheet }
        .sheet(isPresented: $showingTextEditor) { textEditorSheet }
        .refreshable { await reload() }
        .task { if workspaces.isEmpty { await reload() } }
    }

    private func blockView(_ block: SoanBlock, tab: SoanTab, root: String) -> some View {
        let media = (tab.images ?? []).filter { block.contains(line: $0.line) }
        return VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(media.filter { $0.placement == "replace" }.enumerated()), id: \.offset) { _, image in imageView(image, root: root) }
            if !block.text.hasPrefix("⟦IMAGE") && !block.text.hasPrefix("⟦FIGURE") {
                Text(styled(block)).frame(maxWidth: .infinity, alignment: alignment(block.style)).padding(.leading, CGFloat(block.style.pad ?? 0) * 8)
            }
            ForEach(Array(media.filter { $0.placement == "after" }.enumerated()), id: \.offset) { _, image in imageView(image, root: root) }
        }
        .padding(.vertical, block.style.blank == true ? 8 : 2)
        .contentShape(Rectangle())
        .onTapGesture { selectedBlock = block; comment = ""; showingComment = true }
        .listRowBackground(selectedBlock?.id == block.id ? Color.accentColor.opacity(0.10) : Color.clear)
    }

    @ViewBuilder private func imageView(_ image: SoanImage, root: String) -> some View {
        if image.missing != true, let source = image.source, let url = imageURL(root: root, source: source, version: image.version) {
            AsyncImage(url: url) { phase in
                if let rendered = phase.image { rendered.resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 10)) }
                else if phase.error != nil { Label(image.caption, systemImage: "photo.badge.exclamationmark").foregroundStyle(.secondary) }
                else { ProgressView().frame(maxWidth: .infinity) }
            }
            if !image.caption.isEmpty { Text(image.caption).font(.caption).foregroundStyle(.secondary) }
        } else { Label(image.caption.isEmpty ? "画像を取得できません" : image.caption, systemImage: "photo.badge.exclamationmark").foregroundStyle(.secondary) }
    }

    private var commentSheet: some View {
        NavigationStack { Form {
            if let block = selectedBlock { Section("選択したブロック") { Text(block.text).textSelection(.enabled) } }
            Section("LLMへのコメント") { TextField("例：根拠を補い、結論を明確に", text: $comment, axis: .vertical).lineLimit(3...8) }
            Section { Button("修正案を作る") { Task { await editSelectedBlock() } }.disabled(comment.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || loading) }
        }.navigationTitle("ブロックを相談").toolbar { ToolbarItem(placement: .cancellationAction) { Button("閉じる") { showingComment = false } } } }
    }

    private var textEditorSheet: some View {
        NavigationStack { TextEditor(text: $draft).padding().navigationTitle("文字を直接編集").toolbar {
            ToolbarItem(placement: .cancellationAction) { Button("閉じる") { showingTextEditor = false } }
            ToolbarItem(placement: .confirmationAction) { Button("Mac miniへ保存") { Task { await saveCurrent(); showingTextEditor = false } }.disabled(draft == selectedTab?.content || loading) }
        } }
    }

    private func styled(_ block: SoanBlock) -> AttributedString {
        var result = AttributedString()
        for (index, segment) in block.segments.enumerated() {
            var value = AttributedString((index == 0 ? (segment.style.sigil ?? "") + (segment.style.mark ?? "") : "") + segment.text)
            if segment.style.underline == true { value.underlineStyle = .single }
            if segment.style.strikethrough == true { value.strikethroughStyle = .single }
            let highlight = segment.style.hl ?? ""
            var font: Font = .body
            if highlight == "SoanTitle" { font = .title }
            else if highlight == "SoanSubtitle" { font = .title3 }
            else if highlight.hasPrefix("SoanHeading") { font = highlight.hasSuffix("1") ? .title2 : .headline }
            else if let size = segment.style.fontSizePt { font = .system(size: min(max(size, 11), 32)) }
            if segment.style.bold == true || highlight == "SoanTitle" || highlight.hasPrefix("SoanHeading") { font = font.bold() }
            if segment.style.italic == true { font = font.italic() }
            value.font = font
            result.append(value)
        }
        return result
    }
    private func alignment(_ style: SoanLineStyle) -> Alignment { switch style.alignment { case "CENTER": .center; case "END", "RIGHT": .trailing; default: .leading } }
    private func blocks(for tab: SoanTab) -> [SoanBlock] {
        let segments = tab.content.components(separatedBy: "\n").enumerated().map { index, text in SoanSegment(line: index, text: text, style: index < (tab.styles?.count ?? 0) ? tab.styles![index] : .plain) }
        var result: [SoanBlock] = []
        for segment in segments {
            if let last = result.last, let paragraph = segment.style.paragraph, paragraph > 0, last.style.paragraph == paragraph {
                result[result.count - 1] = SoanBlock(id: last.id, segments: last.segments + [segment])
            } else { result.append(SoanBlock(id: segment.line, segments: [segment])) }
        }
        return result
    }
    private func imageURL(root: String, source: String, version: String?) -> URL? {
        let base = URL(string: UserDefaults.standard.string(forKey: "serverURL") ?? "https://sk-mins-mac-mini.tailc193b2.ts.net/")!
        return makeAPIURL(baseURL: base, path: "api/soan/image", queryItems: [URLQueryItem(name: "root", value: root), URLQueryItem(name: "source", value: source), URLQueryItem(name: "v", value: version)])
    }
    private func reload() async { await perform { workspaces = try await APIClient.shared.get("/api/soan/catalog") } }
    private func open(_ workspace: SoanWorkspace) async { await perform { let value: SoanDocument = try await APIClient.shared.post("/api/soan/open", body: SoanRequest(root: workspace.root), as: SoanDocument.self); document = value; select(value.tabs.first) } }
    private func select(_ tab: SoanTab?) { selectedTab = tab; draft = tab?.content ?? ""; selectedBlock = nil }
    private func editSelectedBlock() async {
        guard let document, let tab = selectedTab, let block = selectedBlock else { return }
        let instruction = "次の選択ブロックだけを対象にコメントを反映し、文書のその他の部分と構造マーカーは変更しないでください。\n\n選択ブロック:\n\(block.text)\n\nコメント:\n\(comment.trimmingCharacters(in: .whitespacesAndNewlines))"
        await perform { let value: SoanProposal = try await APIClient.shared.post("/api/soan/edit", body: SoanRequest(root: document.root, tabID: tab.id, instruction: instruction), as: SoanProposal.self, timeout: 130); draft = value.content; showingComment = false; showingTextEditor = true }
    }
    private func saveCurrent() async {
        guard let document, let tab = selectedTab else { return }
        await perform { let _: EmptyResponse = try await APIClient.shared.post("/api/soan/save", body: SoanRequest(root: document.root, tabID: tab.id, content: draft, base: tab.content), as: EmptyResponse.self); let refreshed: SoanDocument = try await APIClient.shared.post("/api/soan/open", body: SoanRequest(root: document.root), as: SoanDocument.self); self.document = refreshed; select(refreshed.tabs.first { $0.id == tab.id }) }
    }
    private func perform(_ action: () async throws -> Void) async { loading = true; error = nil; defer { loading = false }; do { try await action() } catch { self.error = error.localizedDescription } }
}
