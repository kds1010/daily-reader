import Foundation
import SwiftUI
import EventKit
#if os(iOS)
import CoreMotion
import CryptoKit

private struct PhoneCalendarPayload: Codable {
    var state: String; var start_at: String?; var end_at: String?
    var items: [PhoneCalendarEvent] = []
}
private struct PhoneMotionPayload: Codable {
    var state: String; var start_at: String?; var end_at: String?
    var items: [PhoneMotionInterval] = []
}
private struct PhonePayload: Codable {
    let device_id: String; var captured_at: String; let timezone: String
    var calendar: PhoneCalendarPayload; var motion: PhoneMotionPayload
}

@MainActor
private final class MotionReply {
    var continuation: CheckedContinuation<[CMMotionActivity]?, Never>?
    init(_ value: CheckedContinuation<[CMMotionActivity]?, Never>) { continuation = value }
    func finish(_ value: [CMMotionActivity]?) {
        continuation?.resume(returning: value); continuation = nil
    }
}

@MainActor
final class PhoneContextSync: ObservableObject {
    static let shared = PhoneContextSync()
    @Published var message = "初回の許可後に自動取得します。"
    @Published var healthMessage = "HealthKitは一度同期すると、以後の更新時に自動同期します。"
    private let events = EKEventStore()
    private let motion = CMMotionActivityManager()
    private var working = false
    private var lastAttempt: Date = .distantPast
    private var queueURL: URL { URL.applicationSupportDirectory.appending(path: "Daymeld/phone-context-pending.json") }
    private var calendarEnabled: Bool { UserDefaults.standard.object(forKey: "phone.calendar.enabled") as? Bool != false }
    private var motionEnabled: Bool { UserDefaults.standard.object(forKey: "phone.motion.enabled") as? Bool != false }
    private var deviceID: String {
        if let id = UserDefaults.standard.string(forKey: "phone.context.deviceID") { return id }
        let id = UUID().uuidString; UserDefaults.standard.set(id, forKey: "phone.context.deviceID"); return id
    }
    func synchronize(force: Bool = false, requestMotion: Bool = false) async {
        guard !working else { return }
        let lastSuccess = UserDefaults.standard.object(forKey: "phone.context.lastSuccess") as? Date ?? .distantPast
        guard force || (Date.now.timeIntervalSince(lastAttempt) >= 60 && Date.now.timeIntervalSince(lastSuccess) >= 900) else { return }
        working = true; lastAttempt = .now; defer { working = false }
        do {
            if FileManager.default.fileExists(atPath: queueURL.path) {
                var saved = try JSONDecoder().decode(PhonePayload.self, from: Data(contentsOf: queueURL))
                saved.captured_at = repairLegacyQueuedUTCTimestamp(saved.captured_at)
                // Revoked permissions or a disabled switch also apply to queued uploads.
                if !calendarEnabled || EKEventStore.authorizationStatus(for: .event) != .fullAccess {
                    saved.calendar = PhoneCalendarPayload(state: calendarEnabled ? "denied" : "disabled")
                }
                if !motionEnabled || CMMotionActivityManager.authorizationStatus() != .authorized {
                    saved.motion = PhoneMotionPayload(state: motionEnabled ? "denied" : "disabled")
                }
                if let date = lifeDate(saved.captured_at), Date.now.timeIntervalSince(date) < 7 * 86400 {
                    let _: EmptyResponse = try await APIClient.shared.post("api/device-context/sync", body: saved, as: EmptyResponse.self)
                }
                try FileManager.default.removeItem(at: queueURL)
            }
            let now = Date.now
            let calendar = collectCalendar(now: now)
            let activities = await collectMotion(now: now, requestPermission: requestMotion)
            try Task.checkCancellation()
            let payload = PhonePayload(device_id: deviceID, captured_at: preciseUTCTimestamp(now), timezone: TimeZone.current.identifier, calendar: calendar, motion: activities)
            try FileManager.default.createDirectory(at: queueURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try JSONEncoder().encode(payload).write(to: queueURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            let _: EmptyResponse = try await APIClient.shared.post("api/device-context/sync", body: payload, as: EmptyResponse.self)
            try FileManager.default.removeItem(at: queueURL)
            UserDefaults.standard.set(Date.now, forKey: "phone.context.lastSuccess")
            message = "カレンダー: \(collectionLabel(calendar.state, count: calendar.items.count)) ／ 移動: \(collectionLabel(activities.state, count: activities.items.count))。\(Date.now.formatted(date: .omitted, time: .shortened))"
        } catch {
            message = "端末情報は未同期です。保存できた内容は端末に保持し、接続後に再試行します。"
        }
        do { healthMessage = try await HealthAutoSync.shared.synchronize() }
        catch { healthMessage = "HealthKitの自動同期は未完了です: " + error.localizedDescription }
    }
    private func collectionLabel(_ state: String, count: Int) -> String {
        switch state {
        case "available": return "同期済み\(count)件"
        case "disabled": return "取得停止中"
        case "denied": return "読み取り権限なし"
        default: return "未取得"
        }
    }
    private func collectCalendar(now: Date) -> PhoneCalendarPayload {
        guard calendarEnabled else { return PhoneCalendarPayload(state: "disabled") }
        let status = EKEventStore.authorizationStatus(for: .event)
        guard status == .fullAccess else {
            return PhoneCalendarPayload(state: status == .notDetermined ? "unavailable" : "denied")
        }
        let start = Calendar.current.date(byAdding: .day, value: -7, to: Calendar.current.startOfDay(for: now))!
        let end = Calendar.current.date(byAdding: .day, value: 14, to: Calendar.current.startOfDay(for: now))!
        let raw = events.events(matching: events.predicateForEvents(withStart: start, end: end, calendars: nil)).filter { $0.status != .canceled }
        guard raw.count <= 1000 else { return PhoneCalendarPayload(state: "unavailable") }
        var seen = Set<String>()
        let items = raw.compactMap { event -> PhoneCalendarEvent? in
            guard let a = event.startDate, let b = event.endDate, a < b else { return nil }
            let identity = (event.calendarItemExternalIdentifier ?? event.calendarItemIdentifier) + "|" + a.ISO8601Format()
            let id = SHA256.hash(data: Data(identity.utf8)).map { String(format: "%02x", $0) }.joined()
            guard seen.insert(id).inserted else { return nil }
            let blocking = event.availability != .free && (!event.isAllDay || [.busy, .tentative, .unavailable].contains(event.availability))
            let ownID = event.url?.scheme == "daymeld" && event.url?.host == "event" ? event.url!.lastPathComponent : ""
            return PhoneCalendarEvent(id: id, title: String((event.title ?? "予定").prefix(200)), location: String((event.location ?? "").prefix(300)), start_at: a.ISO8601Format(), end_at: b.ISO8601Format(), busy: blocking, all_day: event.isAllDay, daymeld_entry_id: ownID)
        }
        return PhoneCalendarPayload(state: "available", start_at: start.ISO8601Format(), end_at: end.ISO8601Format(), items: items)
    }
    private func collectMotion(now: Date, requestPermission: Bool) async -> PhoneMotionPayload {
        guard motionEnabled else { return PhoneMotionPayload(state: "disabled") }
        guard CMMotionActivityManager.isActivityAvailable() else { return PhoneMotionPayload(state: "unavailable") }
        let status = CMMotionActivityManager.authorizationStatus()
        guard status == .authorized || requestPermission && status == .notDetermined else {
            return PhoneMotionPayload(state: status == .denied || status == .restricted ? "denied" : "unavailable")
        }
        let start = now.addingTimeInterval(-7 * 86400)
        let rows: [CMMotionActivity]? = await withCheckedContinuation { continuation in
            let reply = MotionReply(continuation)
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { reply.finish(nil) }
            motion.queryActivityStarting(from: start, to: now, to: .main) { rows, error in
                reply.finish(error == nil ? rows : nil)
            }
        }
        guard let rows else { return PhoneMotionPayload(state: "unavailable") }
        let sorted = rows.sorted { $0.startDate < $1.startDate }
        var intervals: [PhoneMotionInterval] = []
        for (index, row) in sorted.enumerated() {
            let a = max(start, row.startDate)
            // Long gaps remain unknown rather than extending a stale activity indefinitely.
            let b = min(index + 1 < sorted.count ? sorted[index + 1].startDate : now, a.addingTimeInterval(1800))
            guard a < b else { continue }
            let options = [(row.automotive, "automotive"), (row.cycling, "cycling"), (row.running, "running"), (row.walking, "walking"), (row.stationary, "stationary")].filter { $0.0 }
            let activity = options.count == 1 && !row.unknown ? options[0].1 : "unknown"
            let confidence = row.confidence == .high ? "high" : row.confidence == .medium ? "medium" : "low"
            guard activity != "unknown", confidence != "low" else { continue }
            intervals.append(PhoneMotionInterval(start_at: a.ISO8601Format(), end_at: b.ISO8601Format(), activity: activity, confidence: confidence))
        }
        guard intervals.count <= 3000 else { return PhoneMotionPayload(state: "unavailable") }
        return PhoneMotionPayload(state: "available", start_at: start.ISO8601Format(), end_at: now.ISO8601Format(), items: intervals)
    }
}
#endif

struct PhoneContextPanel: View {
    let overview: PhoneContextOverview?
    var onSync: () async -> Void = {}
    #if os(iOS)
    @ObservedObject private var sync = PhoneContextSync.shared
    @AppStorage("phone.calendar.enabled") private var calendarEnabled = true
    @AppStorage("phone.motion.enabled") private var motionEnabled = true
    @AppStorage("phone.health.enabled") private var healthEnabled = true
    #endif
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            #if os(iOS)
            Toggle("既存カレンダーから予定・空き時間を取得", isOn: $calendarEnabled)
                .onChange(of: calendarEnabled) { _, _ in Task { await sync.synchronize(force: true); await onSync() } }
            Toggle("歩行・移動状態を取得", isOn: $motionEnabled)
                .onChange(of: motionEnabled) { _, _ in Task { await sync.synchronize(force: true); await onSync() } }
            Button("移動状態の初回アクセスを許可") { Task { await sync.synchronize(force: true, requestMotion: true); await onSync() } }
            Toggle("HealthKitの集計を自動同期", isOn: $healthEnabled)
            Text(sync.message).font(.caption)
            Text(sync.healthMessage).font(.caption)
            Text("睡眠は当日正午までの24時間内で、読み取れた睡眠区間を集計します。覚醒・ベッド内時間と重複は除きます。").font(.caption).foregroundStyle(.secondary)
            Button("端末情報を今すぐ同期") { Task { await sync.synchronize(force: true); await onSync() } }
            #endif
            if let overview {
                Text(overview.calendar_ready ? "同期したカレンダーから今日の空き時間を確認できます。" : "カレンダーの許可・新しい同期が必要です。空き時間は未確認です。").font(.caption)
                ForEach(overview.conflicts) { item in
                    Text("時間の重なり: \(item.title) ／ \(item.calendar_title)").font(.caption).foregroundStyle(.orange)
                }
                ForEach(overview.agenda) { event in
                    VStack(alignment: .leading) {
                        Text(event.title)
                        Text(lifeDate(event.start_at)?.formatted() ?? event.start_at).font(.caption)
                    }
                }
            }
            Text("カレンダーの件名・日時・場所と移動区間をMacへ同期します。予定との時刻一致は参加の証明には使いません。健康・移動・カレンダーはCodexへ送りません。").font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct PhoneEvidenceView: View {
    let evidence: PhoneContextEvidence
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(evidence.calendar) { event in
                Text("同時刻のカレンダー: \(event.title)\(event.location.isEmpty ? "" : "（\(event.location)）")").font(.caption)
            }
            ForEach(evidence.motion) { item in
                Text("端末の移動状態: \(activityLabel(item.activity))（推定）").font(.caption)
            }
            if !evidence.calendar.isEmpty || !evidence.motion.isEmpty {
                Text("端末の時刻による関連候補です。実際の参加・同席者や会話の内容を確定しません。").font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    private func activityLabel(_ value: String) -> String {
        ["stationary": "静止", "walking": "歩行", "running": "走行", "cycling": "自転車", "automotive": "自動車等で移動"][value] ?? "不明"
    }
}
