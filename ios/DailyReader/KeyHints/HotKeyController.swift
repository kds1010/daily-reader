import Carbon.HIToolbox

final class HotKeyController {
    var onPress: (() -> Void)?
    private var hotKeyRef: EventHotKeyRef?
    private var handler: EventHandlerRef?
    private var signature = OSType(0x4B484E54)

    func start() {
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, userData in
            guard let userData else { return noErr }
            let controller = Unmanaged<HotKeyController>.fromOpaque(userData).takeUnretainedValue()
            controller.onPress?(); return noErr
        }, 1, &eventType, Unmanaged.passUnretained(self).toOpaque(), &handler)
        var id = EventHotKeyID(signature: signature, id: 1)
        RegisterEventHotKey(UInt32(kVK_Space), UInt32(cmdKey | shiftKey), id, GetApplicationEventTarget(), 0, &hotKeyRef)
    }

    deinit {
        if let hotKeyRef { UnregisterEventHotKey(hotKeyRef) }
        if let handler { RemoveEventHandler(handler) }
    }
}
