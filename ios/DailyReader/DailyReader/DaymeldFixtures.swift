import Foundation

enum DaymeldResource: String, CaseIterable, Hashable {
    case today
    case email
    case agents
    case tanomi
    case codexUsage
    case news
    case deployment
}

struct DaymeldFixture {
    var repositories: [Repository]
    var agentModels: [AgentModelOption]
    var agents: [AgentJob]
    var archivedAgents: [AgentJob]
    var tanomiRepositories: [TanomiRepository]
    var tanomiConfig: TanomiConfig
    var tanomiTasks: [TanomiTask]
    var tanomiArchivedTasks: [TanomiTask]
    var tanomiUsage: TanomiUsage?
    var tanomiAvailable: Bool
    var tanomiStatusMessage: String?
    var today: TodayEnvelope?
    var emails: [EmailReminder]
    var emailContents: [String: EmailThreadContent]
    var articles: [Article]
    var codexUsage: CodexUsageEnvelope?
    var deploymentInfo: DeploymentInfo?
    var failedResources: Set<DaymeldResource>
    var referenceDate: Date?

    init(
        repositories: [Repository] = [],
        agentModels: [AgentModelOption] = [.fallback],
        agents: [AgentJob] = [],
        archivedAgents: [AgentJob] = [],
        tanomiRepositories: [TanomiRepository] = [],
        tanomiConfig: TanomiConfig = TanomiConfig(
            models: ["opus"],
            defaultModel: "opus",
            efforts: ["low", "medium", "high", "xhigh", "max"],
            defaultEffort: nil,
            permissionModes: ["acceptEdits", "plan", "manual", "bypassPermissions"]
        ),
        tanomiTasks: [TanomiTask] = [],
        tanomiArchivedTasks: [TanomiTask] = [],
        tanomiUsage: TanomiUsage? = nil,
        tanomiAvailable: Bool = false,
        tanomiStatusMessage: String? = nil,
        today: TodayEnvelope? = nil,
        emails: [EmailReminder] = [],
        emailContents: [String: EmailThreadContent] = [:],
        articles: [Article] = [],
        codexUsage: CodexUsageEnvelope? = nil,
        deploymentInfo: DeploymentInfo? = nil,
        failedResources: Set<DaymeldResource> = [],
        referenceDate: Date? = nil
    ) {
        self.repositories = repositories
        self.agentModels = agentModels
        self.agents = agents
        self.archivedAgents = archivedAgents
        self.tanomiRepositories = tanomiRepositories
        self.tanomiConfig = tanomiConfig
        self.tanomiTasks = tanomiTasks
        self.tanomiArchivedTasks = tanomiArchivedTasks
        self.tanomiUsage = tanomiUsage
        self.tanomiAvailable = tanomiAvailable
        self.tanomiStatusMessage = tanomiStatusMessage
        self.today = today
        self.emails = emails
        self.emailContents = emailContents
        self.articles = articles
        self.codexUsage = codexUsage
        self.deploymentInfo = deploymentInfo
        self.failedResources = failedResources
        self.referenceDate = referenceDate
    }

#if DEBUG
    static let standard: DaymeldFixture = {
        let day = "2026-09-01"
        let repositories = [
            Repository(name: "daily-reader", label: "Daymeld"),
            Repository(name: "soan", label: "soan"),
            Repository(name: "tonoi", label: "tonoi"),
            Repository(name: "config", label: "設定")
        ]
        let models = [
            AgentModelOption(
                slug: "gpt-5.6-luna",
                displayName: "GPT-5.6-Luna",
                defaultReasoningEffort: "low",
                supportedReasoningEfforts: ["low", "medium", "high"]
            ),
            AgentModelOption(
                slug: "gpt-5.6-sol",
                displayName: "GPT-5.6-Sol",
                defaultReasoningEffort: "medium",
                supportedReasoningEfforts: ["medium", "high", "xhigh"]
            )
        ]
        let events = [
            AgentEvent(createdAt: "2026-09-01T08:40:00+09:00", kind: "user", message: "今日の画面を使いやすく確認してください"),
            AgentEvent(createdAt: "2026-09-01T08:41:00+09:00", kind: "codex", message: "実装とテストデータを確認しています"),
            AgentEvent(createdAt: "2026-09-01T08:42:00+09:00", kind: "system", message: "検証コマンドを準備しました")
        ]
        let agents = [
            AgentJob(
                id: "fixture-queued", repository: "daily-reader", repositoryLabel: "Daymeld",
                prompt: "待機中の依頼を確認する長めのサンプル", status: "queued", phase: "キュー待ち",
                summary: nil, model: "gpt-5.6-luna", reasoningEffort: "low", updatedAt: "2026-09-01T09:00:00+09:00", recentEvents: nil,
                events: events, mode: "execute", followUp: nil, worktree: nil
            ),
            AgentJob(
                id: "fixture-running", repository: "daily-reader", repositoryLabel: "Daymeld",
                prompt: "実行中のタスク：画面状態を調整しています", status: "running", phase: "実装",
                summary: "テスト用fixtureと状態表示を調査中です。", model: "gpt-5.6-sol", reasoningEffort: "xhigh", updatedAt: "2026-09-01T08:58:00+09:00",
                recentEvents: Array(events.suffix(2)), events: events, mode: "execute", followUp: nil, worktree: "/tmp/daymeld-fixture"
            ),
            AgentJob(
                id: "fixture-blocked", repository: "soan", repositoryLabel: "soan",
                prompt: "判断が必要な依頼", status: "blocked", phase: "確認待ち",
                summary: "どの表示密度を標準にするか判断してください。", model: "gpt-5.6-luna", reasoningEffort: "medium", updatedAt: "2026-09-01T08:55:00+09:00",
                recentEvents: [events[0], AgentEvent(createdAt: "2026-09-01T08:55:00+09:00", kind: "codex", message: "表示密度について選択が必要です")],
                events: events, mode: "execute", followUp: nil, worktree: "/tmp/daymeld-blocked"
            ),
            AgentJob(
                id: "fixture-completed", repository: "tonoi", repositoryLabel: "tonoi",
                prompt: "完了済みのタスクについて確認する", status: "completed", phase: "完了",
                summary: "長い完了サマリーを表示し、同じカードから質問できます。", model: "gpt-5.6-sol", reasoningEffort: "high", updatedAt: "2026-09-01T08:50:00+09:00",
                recentEvents: nil, events: events, mode: "execute", followUp: nil, worktree: nil
            ),
            AgentJob(
                id: "fixture-failed", repository: "config", repositoryLabel: "設定",
                prompt: "保持した作業環境から再開できる失敗", status: "failed", phase: "失敗",
                summary: "検証の一部が失敗しました。追加指示で再開できます。", model: "gpt-5.6-luna", reasoningEffort: "low", updatedAt: "2026-09-01T08:45:00+09:00",
                recentEvents: [AgentEvent(createdAt: "2026-09-01T08:45:00+09:00", kind: "system", message: "テストが失敗しました")],
                events: events, mode: "execute", followUp: nil, worktree: "/tmp/daymeld-failed"
            ),
            AgentJob(
                id: "fixture-cancelled", repository: "daily-reader", repositoryLabel: "Daymeld",
                prompt: "キャンセル済みの履歴", status: "cancelled", phase: "キャンセル済み",
                summary: nil, model: "gpt-5.6-luna", reasoningEffort: "low", updatedAt: "2026-08-31T22:10:00+09:00", recentEvents: nil,
                events: [], mode: "execute", followUp: nil, worktree: nil
            )
        ]
        let archivedAgents = (0..<8).map { index in
            AgentJob(
                id: "fixture-archived-\(index)", repository: repositories[index % repositories.count].name,
                repositoryLabel: repositories[index % repositories.count].label,
                prompt: "アーカイブ済みタスク \(index + 1)", status: "completed", phase: "完了",
                summary: index == 0 ? "アーカイブからも内容を確認できます。" : nil,
                model: "gpt-5.6-luna", reasoningEffort: "low", updatedAt: "2026-08-\(String(format: "%02d", max(1, 31 - index)))T12:00:00+09:00",
                recentEvents: nil, events: [], mode: "execute", followUp: nil, worktree: nil
            )
        }
        let tanomiRepositories = [
            TanomiRepository(path: "/workspace/tonoi", label: "tonoi"),
            TanomiRepository(path: "/workspace/daily-reader", label: "Daymeld")
        ]
        let tanomiTasks = [
            TanomiTask(
                id: "tanomi-running", title: "tanomi実行中", prompt: "ログを確認して改善案をまとめる",
                repoPath: "/workspace/tonoi", cwd: nil, status: "running", result: nil, error: nil,
                model: "opus", permissionMode: "acceptEdits", createdAt: 1_788_070_000,
                startedAt: 1_788_070_020, endedAt: nil, sessionID: "fixture-session-running"
            ),
            TanomiTask(
                id: "tanomi-done", title: "tanomi完了", prompt: "完了結果を確認する",
                repoPath: "/workspace/daily-reader", cwd: nil, status: "done", result: String(repeating: "完了結果の長いサンプルです。", count: 24), error: nil,
                model: "opus", permissionMode: "plan", createdAt: 1_788_069_000,
                startedAt: 1_788_069_010, endedAt: 1_788_069_200, sessionID: "fixture-session-done"
            ),
            TanomiTask(
                id: "tanomi-error", title: "失敗したtanomi", prompt: "エラー表示を確認する",
                repoPath: "/workspace/tonoi", cwd: nil, status: "error", result: nil,
                error: "権限が不足しているため処理を完了できませんでした。", model: "opus",
                permissionMode: "manual", createdAt: 1_788_068_000, startedAt: 1_788_068_010,
                endedAt: 1_788_068_020, sessionID: "fixture-session-error"
            ),
            TanomiTask(
                id: "tanomi-stopped", title: nil, prompt: "停止済みタスクの題名フォールバック",
                repoPath: nil, cwd: "/workspace/tonoi", status: "stopped", result: nil, error: nil,
                model: nil, permissionMode: nil, createdAt: nil, startedAt: nil, endedAt: nil, sessionID: nil
            )
        ]
        let tanomiUsage = TanomiUsage(
            limits: [
                "five_hour": TanomiUsageLimit(utilization: 0, resetsAt: "2026-09-01T15:00:00+09:00"),
                "seven_day": TanomiUsageLimit(utilization: 100, resetsAt: nil)
            ], running: 1, stale: false
        )
        let plannerTasks = [
            PlannerTask(id: "fixture-overdue", title: "期限超過タスクの長いタイトルで折返しを確認", dueDate: "2026-08-31", priority: 1, recurrence: "none", completedToday: nil),
            PlannerTask(id: "fixture-today", title: "今日の優先タスク", dueDate: day, priority: 2, recurrence: "none", completedToday: nil),
            PlannerTask(id: "fixture-unscheduled", title: "期限なしのタスク", dueDate: nil, priority: 3, recurrence: "none", completedToday: nil)
        ]
        let routines = [
            PlannerTask(id: "fixture-daily", title: "毎日のルーティン", dueDate: day, priority: 1, recurrence: "daily", completedToday: 1),
            PlannerTask(id: "fixture-weekday", title: "平日のルーティン", dueDate: day, priority: 2, recurrence: "weekdays", completedToday: 0),
            PlannerTask(id: "fixture-weekly", title: "毎週のルーティン", dueDate: day, priority: 3, recurrence: "weekly", completedToday: 0)
        ]
        let health = HealthSnapshot(
            date: day, sleepMinutes: 412, steps: 8432, restingHeartRate: 58,
            hrvMS: 44.2, respiratoryRate: 14.5, fatigue: 3, mood: 4,
            note: "午後は集中時間を短めにします。"
        )
        let emails = [
            EmailReminder(threadID: "fixture-email-important", sender: "重要な相手", subject: "今日中に確認が必要な長い件名のメール", importance: "high", reason: "期限が今日で返信が必要です。", requiredAction: "内容を確認して返信", dueDate: day, status: "unread", receivedAt: "2026-09-01T07:30:00+09:00"),
            EmailReminder(threadID: "fixture-email-normal", sender: "ニュースレター", subject: "通常メール", importance: "normal", reason: "参考情報です。", requiredAction: "必要なら読む", dueDate: nil, status: "snoozed", receivedAt: "2026-08-31T18:10:00+09:00"),
            EmailReminder(threadID: "fixture-email-empty", sender: "本文なしの送信者", subject: "本文が空でも表示できるメール", importance: "normal", reason: "本文取得を確認します。", requiredAction: "対応不要なら閉じる", dueDate: "2026-09-03", status: "awaiting_reply", receivedAt: nil)
        ]
        let emailContents = [
            "fixture-email-important": EmailThreadContent(
                threadID: "fixture-email-important", subject: emails[0].subject, accountEmail: "fixture@example.invalid",
                messages: [EmailMessage(sender: emails[0].sender, receivedAt: emails[0].receivedAt!, body: "これは匿名fixtureの長い本文です。" + String(repeating: "確認事項があります。", count: 50))]
            ),
            "fixture-email-empty": EmailThreadContent(
                threadID: "fixture-email-empty", subject: emails[2].subject, accountEmail: "fixture@example.invalid",
                messages: [EmailMessage(sender: emails[2].sender, receivedAt: "2026-09-01T00:00:00+09:00", body: "")]
            )
        ]
        let articles = (0..<14).map { index in
            Article(
                id: "fixture-article-\(index)",
                title: index == 0 ? "非常に長い日本語タイトルでニュースカードの折返しと操作領域を確認します" : "fixtureニュース記事 \(index + 1)",
                summary: index == 1 ? "" : "匿名fixtureの概要です。公開日時、カテゴリ、画像の有無を確認します。",
                source: index % 2 == 0 ? "公式ソース" : "技術ブログ", category: ["データ・AI", "睡眠", "横浜イベント"][index % 3],
                publishedAt: "2026-09-01T0\(index % 9):00:00+09:00",
                url: URL(string: "https://example.invalid/articles/\(index)")!,
                imageURL: index % 3 == 0 ? URL(string: "https://example.invalid/images/\(index).jpg") : nil
            )
        }
        let codexUsage = CodexUsageEnvelope(
            rateLimits: CodexRateLimits(planType: "prolite"),
            rateLimitsByLimitID: [
                "codex": CodexLimit(limitName: nil, primary: CodexLimitWindow(usedPercent: 12, windowDurationMins: 300, resetsAt: 1_788_084_000), secondary: nil),
                "weekly": CodexLimit(limitName: "週次上限", primary: CodexLimitWindow(usedPercent: 87.5, windowDurationMins: 10080, resetsAt: nil), secondary: nil)
            ]
        )
        return DaymeldFixture(
            repositories: repositories, agentModels: models, agents: agents, archivedAgents: archivedAgents,
            tanomiRepositories: tanomiRepositories,
            tanomiConfig: TanomiConfig(models: ["opus", "sonnet"], defaultModel: "opus", efforts: ["low", "medium", "high"], defaultEffort: "medium", permissionModes: ["acceptEdits", "plan", "manual", "bypassPermissions"]),
            tanomiTasks: tanomiTasks, tanomiArchivedTasks: [tanomiTasks[1]], tanomiUsage: tanomiUsage,
            tanomiAvailable: true, tanomiStatusMessage: nil,
            today: TodayEnvelope(date: day, tasks: plannerTasks, routines: routines, health: health),
            emails: emails, emailContents: emailContents, articles: articles, codexUsage: codexUsage,
            deploymentInfo: DeploymentInfo(version: "0.1.0+fixture", deployedAt: "2026-09-01T08:00:00+09:00", iOSReleaseVersion: "0.1.0", macOSReleaseVersion: "0.1.0"),
            referenceDate: Date(timeIntervalSince1970: 1_788_220_800)
        )
    }()

    static func scenario(_ scenario: DaymeldFixtureScenario) -> DaymeldFixture {
        switch scenario {
        case .standard:
            return .standard
        case .empty:
            var fixture = standard
            fixture.agents = []
            fixture.archivedAgents = []
            fixture.tanomiTasks = []
            fixture.tanomiArchivedTasks = []
            fixture.emails = []
            fixture.articles = []
            fixture.today = TodayEnvelope(date: "2026-09-01", tasks: [], routines: [], health: nil)
            fixture.tanomiAvailable = false
            fixture.tanomiStatusMessage = "fixtureでは利用できません"
            fixture.tanomiUsage = nil
            return fixture
        case .partialFailure:
            var fixture = standard
            fixture.failedResources = [.today, .email, .news, .tanomi]
            fixture.tanomiAvailable = false
            fixture.tanomiStatusMessage = "fixture上流が停止しています"
            return fixture
        case .inFlight:
            var fixture = standard
            fixture.agents = fixture.agents.map { job in
                AgentJob(id: job.id, repository: job.repository, repositoryLabel: job.repositoryLabel, prompt: job.prompt, status: "running", phase: "実行中", summary: nil, model: job.model, reasoningEffort: job.reasoningEffort, updatedAt: job.updatedAt, recentEvents: job.recentEvents, events: job.events, mode: job.mode, followUp: job.followUp, worktree: job.worktree)
            }
            fixture.tanomiTasks = fixture.tanomiTasks.map { task in
                TanomiTask(id: task.id, title: task.title, prompt: task.prompt, repoPath: task.repoPath, cwd: task.cwd, status: "running", result: nil, error: nil, model: task.model, permissionMode: task.permissionMode, createdAt: task.createdAt, startedAt: task.startedAt, endedAt: nil, sessionID: task.sessionID)
            }
            fixture.emails = fixture.emails.map { email in
                EmailReminder(threadID: email.threadID, sender: email.sender, subject: email.subject, importance: email.importance, reason: email.reason, requiredAction: email.requiredAction, dueDate: email.dueDate, status: email.status, receivedAt: email.receivedAt)
            }
            return fixture
        case .stress:
            var fixture = standard
            fixture.agents = (0..<42).map { index in
                let base = standard.agents[index % standard.agents.count]
                return AgentJob(id: "fixture-stress-agent-\(index)", repository: base.repository, repositoryLabel: base.repositoryLabel, prompt: "大量データ用の長い依頼タイトル \(index)：" + String(repeating: "折返し確認 ", count: 8), status: ["queued", "running", "blocked", "completed", "failed"][index % 5], phase: "fixtureフェーズ", summary: index % 3 == 0 ? String(repeating: "長い完了サマリーです。", count: 40) : nil, model: base.model, reasoningEffort: base.reasoningEffort, updatedAt: "2026-09-01T09:\(String(format: "%02d", index % 60)):00+09:00", recentEvents: base.recentEvents, events: base.events, mode: "execute", followUp: nil, worktree: index % 5 == 4 ? "/tmp/stress-\(index)" : nil)
            }
            let tanomiStatuses = ["queued", "running", "done", "error", "stopped"]
            fixture.tanomiTasks = (0..<30).map { index -> TanomiTask in
                let bucket = index % tanomiStatuses.count
                let result: String? = bucket == 2 ? String(repeating: "長い結果本文です。", count: 500) : nil
                let error: String? = bucket == 3 ? String(repeating: "エラー詳細です。", count: 80) : nil
                let endedAt: Double? = index % 2 == 0 ? 1_788_060_020 + Double(index) : nil
                let sessionID: String? = bucket == 0 ? nil : "stress-session-\(index)"
                return TanomiTask(
                    id: "fixture-stress-tanomi-\(index)", title: "大量tanomiタスク \(index)", prompt: "長文プロンプト",
                    repoPath: "/workspace/tonoi", cwd: nil, status: tanomiStatuses[bucket], result: result, error: error,
                    model: "opus", permissionMode: "acceptEdits", createdAt: 1_788_060_000 + Double(index),
                    startedAt: 1_788_060_010 + Double(index), endedAt: endedAt, sessionID: sessionID
                )
            }
            fixture.archivedAgents = fixture.agents
            fixture.tanomiArchivedTasks = Array(fixture.tanomiTasks.prefix(10))
            fixture.emails = (0..<30).map { index in
                EmailReminder(threadID: "fixture-stress-email-\(index)", sender: "送信者 \(index)", subject: "長い件名 \(index)：" + String(repeating: "折返し ", count: 10), importance: index % 4 == 0 ? "high" : "normal", reason: "理由", requiredAction: "対応を確認", dueDate: index % 3 == 0 ? "2026-09-01" : nil, status: "unread", receivedAt: "2026-09-01T07:00:00+09:00")
            }
            fixture.articles = (0..<120).map { index in
                Article(id: "fixture-stress-article-\(index)", title: "大量ニュース \(index)：" + String(repeating: "長いタイトル ", count: 7), summary: String(repeating: "概要 ", count: 40), source: "fixtureソース", category: ["データ・AI", "子育て", "睡眠", "筋トレ"][index % 4], publishedAt: "2026-09-01T08:00:00+09:00", url: URL(string: "https://example.invalid/stress/\(index)")!, imageURL: index % 2 == 0 ? URL(string: "https://example.invalid/stress/\(index).jpg") : nil)
            }
            fixture.today = TodayEnvelope(date: "2026-09-01", tasks: (0..<12).map { index in PlannerTask(id: "fixture-stress-task-\(index)", title: "大量タスク \(index)：" + String(repeating: "長いタイトル ", count: 6), dueDate: index % 3 == 0 ? "2026-08-30" : nil, priority: index % 3 + 1, recurrence: "none", completedToday: nil) }, routines: standard.today?.routines ?? [], health: standard.today?.health)
            return fixture
        }
    }

    static func fromProcessArguments() -> DaymeldFixture? {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "-daymeld-fixture"), index + 1 < arguments.count,
              let selectedScenario = DaymeldFixtureScenario(rawValue: arguments[index + 1]) else { return nil }
        return DaymeldFixture.scenario(selectedScenario)
    }
#endif
}

#if DEBUG
enum DaymeldFixtureScenario: String, CaseIterable {
    case standard
    case empty
    case partialFailure = "partial-failure"
    case stress
    case inFlight = "in-flight"
}
#endif

extension TanomiRepository {
    init(path: String, label: String?) {
        self.path = path
        self.label = label
    }
}
