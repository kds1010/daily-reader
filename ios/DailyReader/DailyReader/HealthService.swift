import Foundation
import HealthKit

actor HealthService {
    private let store = HKHealthStore()

    private var types: Set<HKObjectType> { Set([
            HKObjectType.quantityType(forIdentifier: .stepCount),
            HKObjectType.quantityType(forIdentifier: .restingHeartRate),
            HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN),
            HKObjectType.quantityType(forIdentifier: .respiratoryRate),
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis),
        ].compactMap { $0 }) }

    func previouslyRequested() async -> Bool {
        guard HKHealthStore.isHealthDataAvailable() else { return false }
        return await withCheckedContinuation { continuation in
            store.getRequestStatusForAuthorization(toShare: [], read: types) { status, error in
                continuation.resume(returning: error == nil && status == .unnecessary)
            }
        }
    }
    func readToday(requestAuthorization: Bool = true) async throws -> HealthSnapshot {
        guard HKHealthStore.isHealthDataAvailable() else { throw HealthError.unavailable }
        if requestAuthorization { try await store.requestAuthorization(toShare: [], read: types) }
        let start = Calendar.current.startOfDay(for: .now)
        let end = Date()
        async let steps: Double? = try? sum(.stepCount, unit: .count(), start: start, end: end)
        async let resting: Double? = try? average(.restingHeartRate, unit: HKUnit.count().unitDivided(by: .minute()), start: start, end: end)
        async let hrv: Double? = try? average(.heartRateVariabilitySDNN, unit: .secondUnit(with: .milli), start: start, end: end)
        async let respiratory: Double? = try? average(.respiratoryRate, unit: HKUnit.count().unitDivided(by: .minute()), start: start, end: end)
        let sleepEnd = Calendar.current.date(bySettingHour: 12, minute: 0, second: 0, of: end)!
        async let sleep: Double? = try? sleepMinutes(start: sleepEnd.addingTimeInterval(-24 * 3600), end: min(end, sleepEnd))
        let readings = await (steps, resting, hrv, respiratory, sleep)
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current; formatter.dateFormat = "yyyy-MM-dd"
        return HealthSnapshot(
            date: formatter.string(from: end),
            sleepMinutes: readings.4.map(Int.init), steps: readings.0.map(Int.init),
            restingHeartRate: readings.1, hrvMS: readings.2,
            respiratoryRate: readings.3
        )
    }

    private func sum(_ id: HKQuantityTypeIdentifier, unit: HKUnit, start: Date, end: Date) async throws -> Double? {
        try await statistic(id, option: .cumulativeSum, unit: unit, start: start, end: end)
    }

    private func average(_ id: HKQuantityTypeIdentifier, unit: HKUnit, start: Date, end: Date) async throws -> Double? {
        try await statistic(id, option: .discreteAverage, unit: unit, start: start, end: end)
    }

    private func statistic(_ id: HKQuantityTypeIdentifier, option: HKStatisticsOptions, unit: HKUnit, start: Date, end: Date) async throws -> Double? {
        guard let type = HKQuantityType.quantityType(forIdentifier: id) else { return nil }
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end)
        return try await withCheckedThrowingContinuation { continuation in
            store.execute(HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: option) { _, result, error in
                if let error { continuation.resume(throwing: error); return }
                let quantity = option == .cumulativeSum ? result?.sumQuantity() : result?.averageQuantity()
                continuation.resume(returning: quantity?.doubleValue(for: unit))
            })
        }
    }

    private func sleepMinutes(start: Date, end: Date) async throws -> Double? {
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else { return nil }
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end)
        return try await withCheckedThrowingContinuation { continuation in
            store.execute(HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, samples, error in
                if let error { continuation.resume(throwing: error); return }
                let values = Set([HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue,
                                  HKCategoryValueSleepAnalysis.asleepCore.rawValue,
                                  HKCategoryValueSleepAnalysis.asleepDeep.rawValue,
                                  HKCategoryValueSleepAnalysis.asleepREM.rawValue])
                let asleep = (samples as? [HKCategorySample] ?? []).filter { values.contains($0.value) }
                let intervals = asleep.filter { $0.endDate > $0.startDate }.map { DateInterval(start: $0.startDate, end: $0.endDate) }
                continuation.resume(returning: mergedSleepMinutes(intervals, window: DateInterval(start: start, end: end)))
            })
        }
    }
}

enum HealthError: LocalizedError { case unavailable; var errorDescription: String? { "この端末ではHealthKitを利用できません" } }


actor HealthAutoSync {
    static let shared = HealthAutoSync()
    private let health = HealthService()
    private var working = false
    private var lastAttempt: Date = .distantPast
    private var message = "HealthKitは「今日」で一度同期すると自動更新します。"
    func synchronize(force: Bool = false, requestAuthorization: Bool = false) async throws -> String {
        if !requestAuthorization && UserDefaults.standard.object(forKey: "phone.health.enabled") as? Bool == false { return "HealthKitの自動同期は停止しています。" }
        if !requestAuthorization && !UserDefaults.standard.bool(forKey: "phone.health.requested") {
            guard await health.previouslyRequested() else { return message }
            UserDefaults.standard.set(true, forKey: "phone.health.requested")
        }
        guard !working, force || Date.now.timeIntervalSince(lastAttempt) >= 1800 else { return message }
        working = true; lastAttempt = .now; defer { working = false }
        let token = try SecretStore.readHealthToken() ?? ""
        guard !token.isEmpty else { throw APIClientError.server("設定でHealthKit同期トークンを入力してください") }
        let snapshot = try await health.readToday(requestAuthorization: requestAuthorization)
        if requestAuthorization { UserDefaults.standard.set(true, forKey: "phone.health.requested") }
        guard snapshot.sleepMinutes != nil || snapshot.steps != nil || snapshot.restingHeartRate != nil || snapshot.hrvMS != nil || snapshot.respiratoryRate != nil else {
            message = "読み取れるHealthKitデータがありません。未取得を0として保存しません。"
            return message
        }
        try await APIClient.shared.syncHealth(snapshot, token: token)
        message = "HealthKit同期済み: " + Date.now.formatted(date: .omitted, time: .shortened)
        return message
    }
}
