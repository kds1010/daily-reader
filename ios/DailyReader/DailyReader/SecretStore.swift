import Foundation
import Security

enum SecretStore {
    private static let service = "net.skmin.DailyReader"
    private static let healthAccount = "health-sync-token"
    private static let bootstrapFilename = "health-sync-token.txt"

    /// Imports a token provisioned into the development app's Documents directory.
    /// The plaintext bootstrap file is deleted only after Keychain persistence succeeds.
    @discardableResult
    static func importBootstrapHealthToken() throws -> Bool {
        let documents = try FileManager.default.url(
            for: .documentDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let bootstrap = documents.appending(path: bootstrapFilename)
        guard FileManager.default.fileExists(atPath: bootstrap.path) else { return false }
        let token = try String(contentsOf: bootstrap, encoding: .utf8)
        try saveHealthToken(token)
        try FileManager.default.removeItem(at: bootstrap)
        return true
    }

    static func saveHealthToken(_ token: String) throws {
        let value = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let data = value.data(using: .utf8) else { throw SecretStoreError.encoding }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: healthAccount,
        ]
        SecItemDelete(query as CFDictionary)
        var inserted = query
        inserted[kSecValueData as String] = data
        inserted[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(inserted as CFDictionary, nil)
        guard status == errSecSuccess else { throw SecretStoreError.keychain(status) }
    }

    static func readHealthToken() throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: healthAccount,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data, let value = String(data: data, encoding: .utf8) else {
            throw SecretStoreError.keychain(status)
        }
        return value
    }
}

enum SecretStoreError: LocalizedError {
    case encoding
    case keychain(OSStatus)
    var errorDescription: String? {
        switch self {
        case .encoding: "トークンを保存できませんでした"
        case .keychain(let status): "Keychainエラー（\(status)）"
        }
    }
}
