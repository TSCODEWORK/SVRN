import AppKit

// Strong reference keeps the delegate alive for the lifetime of the app.
let delegate = AppDelegate()
NSApplication.shared.delegate = delegate
NSApp.run()
