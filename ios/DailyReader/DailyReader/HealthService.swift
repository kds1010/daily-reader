import Foundation
import HealthKit

actor HealthService {
    private let store = HKHealthStore()

    func readToday() async throws -> HealthSnapshot {
        guard HKHealthStore.isHealthDataAvailable() else { throw HealthError.unavailable }
        let types = Set([
            HKObjectType.quantityType(forIdentifier: .stepCount),
            HKObjectType.quantityType(forIdentifier: .restingHeartRate),
            HKObjectType.quantityType(forIdentifier: .heartRateVariabilitySDNN),
            HKObjectType.quantityType(forIdentifier: .respiratoryRate),
            HKObjectType.categoryType(forIdentifier: .sleepAnalysis),
        ].compactMap { $0 })
        try await store.requestAuthorization(toShare: [], read: types)
        let start = Calendar.current.startOfDay(for: .now)
        let end = Date()
        async let steps: Double? = try? sum(.stepCount, unit: .count(), start: start, end: end)
        async let resting: Double? = try? average(.restingHeartRate, unit: HKUnit.count().unitDivided(by: .minute()), start: start, end: end)
        async let hrv: Double? = try? average(.heartRateVariabilitySDNN, unit: .secondUnit(with: .milli), start: start, end: end)
        async let respiratory: Double? = try? average(.respiratoryRate, unit: HKUnit.count().unitDivided(by: .minute()), start: start, end: end)
        async let sleep: Double? = try? sleepMinutes(start: start.addingTimeInterval(-12 * 3600), end: end)
        let readings = await (steps, resting, hrv, respiratory, sleep)
        return HealthSnapshot(
            date: Date.now.formatted(.iso8601.year().month().day()),
            sleepMinutes: readings.4.map(Int.init), steps: readings.0.map(Int.init),
            restingHeartRate: readings.1, hrvMS: readings.2,
            respiratoryRate: readings.3
        )
    }

    private func sum(_ id: HKQuantityTypeIdentifier, unit: HKUnit, start: Date, end: Date) async throws -> Double {
        try await statistic(id, option: .cumulativeSum, unit: unit, start: start, end: end)
    }

    private func average(_ id: HKQuantityTypeIdentifier, unit: HKUnit, start: Date, end: Date) async throws -> Double {
        try await statistic(id, option: .discreteAverage, unit: unit, start: start, end: end)
    }

    private func statistic(_ id: HKQuantityTypeIdentifier, option: HKStatisticsOptions, unit: HKUnit, start: Date, end: Date) async throws -> Double {
        guard let type = HKQuantityType.quantityType(forIdentifier: id) else { return 0 }
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end)
        return try await withCheckedThrowingContinuation { continuation in
            store.execute(HKStatisticsQuery(quantityType: type, quantitySamplePredicate: predicate, options: option) { _, result, error in
                if let error { continuation.resume(throwing: error); return }
                let quantity = option == .cumulativeSum ? result?.sumQuantity() : result?.averageQuantity()
                continuation.resume(returning: quantity?.doubleValue(for: unit) ?? 0)
            })
        }
    }

    private func sleepMinutes(start: Date, end: Date) async throws -> Double {
        guard let type = HKCategoryType.categoryType(forIdentifier: .sleepAnalysis) else { return 0 }
        let predicate = HKQuery.predicateForSamples(withStart: start, end: end)
        return try await withCheckedThrowingContinuation { continuation in
            store.execute(HKSampleQuery(sampleType: type, predicate: predicate, limit: HKObjectQueryNoLimit, sortDescriptors: nil) { _, samples, error in
                if let error { continuation.resume(throwing: error); return }
                let asleep = (samples as? [HKCategorySample])?.filter { $0.value != HKCategoryValueSleepAnalysis.inBed.rawValue } ?? []
                continuation.resume(returning: asleep.reduce(0) { $0 + $1.endDate.timeIntervalSince($1.startDate) } / 60)
            })
        }
    }
}

enum HealthError: LocalizedError { case unavailable; var errorDescription: String? { "この端末ではHealthKitを利用できません" } }
