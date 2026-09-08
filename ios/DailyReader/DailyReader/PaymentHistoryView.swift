import SwiftUI
import UniformTypeIdentifiers

struct PaymentHistoryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var importing = false
    @State private var saving = false
    @State private var loading = false
    @State private var items: [PaymentRecord] = []
    @State private var total = 0
    @State private var nextOffset = 0
    @State private var hasMore = false
    @State private var lastImportedAt: String?
    @State private var missingIDs = 0
    @State private var result: PaymentImportResult?
    @State private var error: String?
    @State private var filterDates = false
    @State private var start = Date.now
    @State private var end = Date.now
    @State private var activeStart: String?
    @State private var activeEnd: String?

    private var busy: Bool { saving || loading }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                importCard
                if let result { importResult(result) }
                if let error {
                    Text(error).foregroundStyle(.orange)
                    Button("明細を再読み込み") { Task { await load() } }.disabled(busy)
                }
                dateFilter
                Text("\(items.count) / \(total)件 · 日時は日本時間")
                    .appFont(.caption).foregroundStyle(.secondary)
                if missingIDs > 0 {
                    Text("取引番号なしの明細が全期間で\(missingIDs)件あります。別ファイル間では重複確認が必要です。")
                        .appFont(.caption).foregroundStyle(.orange)
                }
                if loading { ProgressView("明細を読み込み中…") }
                if items.isEmpty && !loading && error == nil {
                    ContentUnavailableView("明細はありません", systemImage: "yensign.circle", description: Text("PayPayからダウンロードしたCSVを取り込むか、表示期間を変更してください。"))
                }
                ForEach(items) { item in paymentRow(item).glassCard() }
                if hasMore {
                    Button("さらに100件を表示") { Task { await load(more: true) } }.disabled(busy)
                }
            }.padding()
        }
        .background(AppBackground())
        .navigationTitle("PayPay明細")
        .toolbar {
            Button { Task { await load() } } label: { Image(systemName: "arrow.clockwise") }
                .disabled(busy)
        }
        .task { await load() }
        .refreshable { await load() }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.commaSeparatedText], allowsMultipleSelection: false) { selection in
            switch selection {
            case .success(let urls):
                if let url = urls.first { Task { await importFile(url) } }
            case .failure:
                error = "CSVを開けませんでした。ファイルを再度選択してください。"
            }
        }
    }

    private var importCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("PayPayからCSVを取り込む").appFont(.headline)
            Text("PayPayの「取引履歴」で期間を指定してダウンロードし、ここでCSVを選択してください。自動取得は行いません。")
                .appFont(.subheadline)
            Link("PayPay公式のダウンロード手順", destination: URL(string: "https://paypay.ne.jp/help/c0447/")!)
            Text("保存先はご自身のMac miniです。明細はAIへ送信しません。購入商品の内訳や正確な残高を示すものではありません。")
                .appFont(.caption).foregroundStyle(.secondary)
            Text(lastImportedAt.map { "最終取り込み: \(displayDate($0))" } ?? "まだ取り込んでいません")
                .appFont(.caption)
            Button { importing = true } label: { Label("CSVを選択して取り込む", systemImage: "square.and.arrow.down") }
                .buttonStyle(.borderedProminent).disabled(busy || model.isFixture)
            if saving { ProgressView("取り込み中…") }
            if model.isFixture { Text("プレビューでは実データを取り込みません。").appFont(.caption) }
        }.glassCard()
    }

    private var dateFilter: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("期間を指定", isOn: $filterDates)
            if filterDates {
                DatePicker("開始日", selection: $start, displayedComponents: .date)
                DatePicker("終了日", selection: $end, displayedComponents: .date)
            }
            Button("表示期間を適用") {
                activeStart = filterDates ? dateKey(start) : nil
                activeEnd = filterDates ? dateKey(end) : nil
                Task { await load() }
            }.disabled(busy || (filterDates && dateKey(start) > dateKey(end)))
            Text(activeStart.map { "表示中: \($0) 〜 \(activeEnd ?? "")" } ?? "表示中: 全期間")
                .appFont(.caption).foregroundStyle(.secondary)
        }.environment(\.timeZone, TimeZone(identifier: "Asia/Tokyo")!)
            .disabled(busy)
    }

    private func importResult(_ value: PaymentImportResult) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(value.file_duplicate ? "このファイルは取り込み済みです" : "CSVの確認結果").appFont(.headline)
            Text("追加 \(value.added)件 · 重複 \(value.duplicates)件 · 競合 \(value.conflicts)件")
            if value.conflicts > 0 {
                Text("同じ取引番号で内容が異なる明細は保存していません。元CSVの該当行をご確認ください。既存の明細は保持しています。")
                Text("競合行（先頭100件まで）: " + value.conflict_rows.map(String.init).joined(separator: ", "))
            }
            if value.missing_ids > 0 {
                Text("取引番号なし \(value.missing_ids)件。同じファイルの再送は重複しませんが、別ファイルの明細とは自動統合しません。")
            }
        }.appFont(.subheadline).glassCard()
    }

    private func paymentRow(_ item: PaymentRecord) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(item.counterparty.isEmpty || item.counterparty == "-" ? "取引先の記載なし" : item.counterparty)
                .appFont(.headline)
            Text("\(item.details["取引日"] ?? displayDate(item.occurred_at)) · \(item.kind)").appFont(.subheadline)
            HStack {
                if let outgoing = item.outgoing_yen { Text("出金 \(outgoing.formatted())円") }
                if let incoming = item.incoming_yen { Text("入金 \(incoming.formatted())円") }
                if item.outgoing_yen == nil && item.incoming_yen == nil { Text("円金額の記載なし") }
            }.appFont(.headline)
            Text(item.payment_method).appFont(.caption).foregroundStyle(.secondary)
            DisclosureGroup("CSVの詳細") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("出所: PayPay CSV · 行 \(item.row_number)")
                    ForEach(item.details.keys.sorted(), id: \.self) { key in
                        Text("\(key): \(item.details[key] ?? "")").textSelection(.enabled)
                    }
                }.appFont(.caption).frame(maxWidth: .infinity, alignment: .leading)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    @MainActor
    private func importFile(_ url: URL) async {
        guard !busy && !model.isFixture else { return }
        saving = true
        error = nil
        result = nil
        do {
            result = try await APIClient.shared.importPayPayCSV(url)
            // Show older imported data too, independently of the previous filter.
            activeStart = nil; activeEnd = nil; filterDates = false
            saving = false
            await load()
        } catch APIClientError.server(let message) {
            self.error = message
        } catch {
            self.error = "CSVを取り込めませんでした。ファイルとMac miniへの接続を確認してください。同じファイルで再試行できます。"
        }
        saving = false
    }

    @MainActor
    private func load(more: Bool = false) async {
        guard !busy else { return }
        loading = true
        error = nil
        defer { loading = false }
        if !more { items = []; total = 0; nextOffset = 0; hasMore = false }
        if model.isFixture { return }
        var query = [URLQueryItem(name: "offset", value: String(more ? nextOffset : 0))]
        if let activeStart { query.append(URLQueryItem(name: "start", value: activeStart)) }
        if let activeEnd { query.append(URLQueryItem(name: "end", value: activeEnd)) }
        do {
            let response: PaymentHistoryResponse = try await APIClient.shared.get("api/payments", queryItems: query)
            let existing = Set(items.map(\.id))
            items += response.items.filter { !existing.contains($0.id) }
            total = response.total
            nextOffset = response.offset + response.items.count
            hasMore = response.has_more
            lastImportedAt = response.last_imported_at
            missingIDs = response.missing_ids
        } catch {
            self.error = "明細を取得できませんでした。Mac miniへの接続を確認して再試行してください。"
        }
    }

    private func dateKey(_ value: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "Asia/Tokyo")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: value)
    }

    private func displayDate(_ value: String) -> String {
        guard let date = parseISOTimestamp(value) else { return value }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ja_JP")
        formatter.timeZone = TimeZone(identifier: "Asia/Tokyo")
        formatter.dateFormat = "yyyy/MM/dd HH:mm:ss"
        return formatter.string(from: date)
    }
}
