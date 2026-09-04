import SwiftUI

struct SoanWorkspace: Codable, Identifiable, Hashable {
    var id: String { root }
    let documentID: String
    let title: String
    let root: String
    let access: String?
}

struct SoanTab: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let content: String
}

struct SoanDocument: Codable {
    let documentID: String
    let title: String
    let revisionID: String
    let root: String
    let tabs: [SoanTab]
}

private struct SoanRequest: Encodable {
    let root: String
    var tabID: String? = nil
    var content: String? = nil
    var instruction: String? = nil
    var base: String? = nil
}

private struct SoanProposal: Decodable { let content: String; let llm: String }

struct SoanView: View {
    @State private var workspaces: [SoanWorkspace] = []
    @State private var document: SoanDocument?
    @State private var selectedTab: SoanTab?
    @State private var draft = ""
    @State private var instruction = ""
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        List {
            Section("文書") {
                if workspaces.isEmpty && !loading {
                    ContentUnavailableView("Soan文書がありません", systemImage: "doc.text", description: Text("Mac miniのSoan接続を確認してください。"))
                }
                ForEach(workspaces) { workspace in
                    Button { Task { await open(workspace) } } label: {
                        HStack { Text(workspace.title); Spacer(); Image(systemName: "chevron.right").foregroundStyle(.tertiary) }
                    }.buttonStyle(.plain)
                }
            }
            if let document, let selectedTab {
                Section(document.title) {
                    if document.tabs.count > 1 {
                        Picker("タブ", selection: Binding(get: { selectedTab.id }, set: { id in select(document.tabs.first { $0.id == id }) })) {
                            ForEach(document.tabs) { Text($0.title).tag($0.id) }
                        }
                    }
                    TextEditor(text: $draft).frame(minHeight: 280)
                    if draft != selectedTab.content { Label("未保存の変更", systemImage: "circle.fill").foregroundStyle(.orange) }
                }
                Section("LLMで改訂") {
                    TextField("例：結論を先にして簡潔に", text: $instruction, axis: .vertical).lineLimit(2...6)
                    Button("改訂案を作る") { Task { await edit(document, selectedTab) } }.disabled(loading || instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button("Mac miniへ保存") { Task { await save(document, selectedTab) } }.buttonStyle(.borderedProminent).disabled(loading || draft == selectedTab.content)
                }
            }
            if loading { HStack { Spacer(); ProgressView(); Spacer() } }
            if let error { Section { Text(error).foregroundStyle(.orange); Button("再読み込み") { Task { await reload() } } } }
        }
        .navigationTitle("資料")
        .refreshable { await reload() }
        .task { if workspaces.isEmpty { await reload() } }
    }

    private func reload() async {
        await perform { workspaces = try await APIClient.shared.get("/api/soan/catalog") }
    }
    private func open(_ workspace: SoanWorkspace) async {
        await perform {
            let value: SoanDocument = try await APIClient.shared.post("/api/soan/open", body: SoanRequest(root: workspace.root), as: SoanDocument.self)
            document = value; select(value.tabs.first)
        }
    }
    private func select(_ tab: SoanTab?) { selectedTab = tab; draft = tab?.content ?? "" }
    private func edit(_ document: SoanDocument, _ tab: SoanTab) async {
        await perform {
            let value: SoanProposal = try await APIClient.shared.post("/api/soan/edit", body: SoanRequest(root: document.root, tabID: tab.id, instruction: instruction), as: SoanProposal.self, timeout: 130)
            draft = value.content; instruction = ""
        }
    }
    private func save(_ document: SoanDocument, _ tab: SoanTab) async {
        await perform {
            let _: EmptyResponse = try await APIClient.shared.post("/api/soan/save", body: SoanRequest(root: document.root, tabID: tab.id, content: draft, base: tab.content), as: EmptyResponse.self)
            let refreshed: SoanDocument = try await APIClient.shared.post("/api/soan/open", body: SoanRequest(root: document.root), as: SoanDocument.self)
            self.document = refreshed; select(refreshed.tabs.first { $0.id == tab.id })
        }
    }
    private func perform(_ action: () async throws -> Void) async {
        loading = true; error = nil; defer { loading = false }
        do { try await action() } catch { self.error = error.localizedDescription }
    }
}
