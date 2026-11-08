// Pepe's ears on the Mac, until the box's speakerphone: listen once (after the key press, the stand-in for the
// button), stop when they stop talking, and print what was said as one JSON line.
// Speech recognition: Apple's service (on-device only with KORE_EARS_ON_DEVICE=1; see below).
//
// Built by kore/home/mac.py with swiftc; usage: kore-ears [es-ES]
// Output: {"ready": true} once listening, then {"text": "...", "on_device": true, "silence_ms": 1300}
//         or {"error": "mic_permission" | "speech_permission" | "no_recognizer" | "mic_start"}

import AVFoundation
import Foundation
import Speech

setvbuf(stdout, nil, _IONBF, 0)
print("{\"pid\": \(getpid())}")  // so the caller can stop this app if it waits too long

func emit(_ dict: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: dict), let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}

func finish(_ dict: [String: Any]) -> Never {
    emit(dict)
    exit(0)
}

let localeId = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "es-ES"
let maxSeconds = 20.0       // a long story is cut here; they can carry on with another press
let startTimeout = 8.0      // nothing said after the press
let silenceAfter = 1.3      // this much quiet after speaking ends the turn (older people pause: not shorter)

// Permissions (the first time, macOS asks).
let auth = DispatchSemaphore(value: 0)
var speechOK = false
SFSpeechRecognizer.requestAuthorization { status in
    speechOK = status == .authorized
    auth.signal()
}
if auth.wait(timeout: .now() + 60) == .timedOut { finish(["error": "permission_timeout"]) }
if !speechOK { finish(["error": "speech_permission"]) }
let mic = DispatchSemaphore(value: 0)
var micOK = false
AVCaptureDevice.requestAccess(for: .audio) { granted in
    micOK = granted
    mic.signal()
}
if mic.wait(timeout: .now() + 60) == .timedOut { finish(["error": "permission_timeout"]) }
if !micOK { finish(["error": "mic_permission"]) }

guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeId)), recognizer.isAvailable else {
    finish(["error": "no_recognizer"])
}
// On-device only when asked (KORE_EARS_ON_DEVICE=1): on this Mac the on-device Spanish model gave empty or wrong
// text while Apple's service was exact (2026-11-08). Audio goes to Apple only after the key press.
let onDevice = recognizer.supportsOnDeviceRecognition && ProcessInfo.processInfo.environment["KORE_EARS_ON_DEVICE"] == "1"
let request = SFSpeechAudioBufferRecognitionRequest()
request.shouldReportPartialResults = true
request.requiresOnDeviceRecognition = onDevice
request.addsPunctuation = true

let lock = NSLock()
var text = ""
var finished = false
var heard = false
var lastVoice = Date()
var noise: Float = 0
var noiseFrames = 0
let started = Date()

let engine = AVAudioEngine()
let input = engine.inputNode
let format = input.outputFormat(forBus: 0)
input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
    request.append(buffer)
    guard let samples = buffer.floatChannelData?[0] else { return }
    let n = Int(buffer.frameLength)
    var sum: Float = 0
    for i in 0..<n { sum += samples[i] * samples[i] }
    let rms = (sum / Float(max(n, 1))).squareRoot()
    lock.lock()
    if Date().timeIntervalSince(started) < 0.3 {  // the room's own noise, before they start
        noise = (noise * Float(noiseFrames) + rms) / Float(noiseFrames + 1)
        noiseFrames += 1
    } else if rms > max(0.006, noise * 2.5) {
        heard = true
        lastVoice = Date()
    }
    lock.unlock()
}
engine.prepare()
do { try engine.start() } catch { finish(["error": "mic_start"]) }
emit(["ready": true])

let task = recognizer.recognitionTask(with: request) { result, error in
    lock.lock()
    if let result = result {
        text = result.bestTranscription.formattedString
        if result.isFinal { finished = true }
    }
    if error != nil { finished = true }
    lock.unlock()
}

while true {
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    lock.lock()
    let (h, lv, f) = (heard, lastVoice, finished)
    lock.unlock()
    let now = Date()
    if f { break }
    if !h && now.timeIntervalSince(started) > startTimeout { break }
    if h && now.timeIntervalSince(lv) > silenceAfter { break }
    if now.timeIntervalSince(started) > maxSeconds { break }
}
engine.stop()
input.removeTap(onBus: 0)
request.endAudio()
let deadline = Date().addingTimeInterval(2.0)  // the final words
while Date() < deadline {
    lock.lock()
    let f = finished
    lock.unlock()
    if f { break }
    RunLoop.current.run(until: Date().addingTimeInterval(0.05))
}
task.cancel()
lock.lock()
let said = text
let quietFor = Int(Date().timeIntervalSince(lastVoice) * 1000)
lock.unlock()
finish(["text": said, "on_device": onDevice, "silence_ms": quietFor])
