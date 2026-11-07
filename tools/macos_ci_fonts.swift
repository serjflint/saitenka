import CoreGraphics
import CoreText
import CryptoKit
import Foundation

let fontName = "PingFangSC-Regular" as CFString

func readableFontURL() -> URL? {
    let available = CTFontManagerCopyAvailableFontURLs() as? [URL] ?? []
    let files = Set(available.map { URL(fileURLWithPath: $0.path) })
    for url in files {
        let descriptors = CTFontManagerCreateFontDescriptorsFromURL(url as CFURL)
            as? [CTFontDescriptor] ?? []
        guard descriptors.contains(where: {
            CTFontDescriptorCopyAttribute($0, kCTFontNameAttribute) as? String == fontName as String
        }), let provider = CGDataProvider(url: url as CFURL), CGFont(provider) != nil else { continue }
        return url
    }
    return nil
}

if readableFontURL() == nil {
    // The installed UI face lacks FreeType-readable outlines: developer.apple.com/forums/thread/758189.
    let descriptor = CTFontDescriptorCreateWithAttributes([
        kCTFontNameAttribute: fontName,
        kCTFontDownloadableAttribute: true,
        kCTFontDownloadedAttribute: false,
    ] as CFDictionary)
    var callbacks = kCFTypeSetCallBacks
    let required = CFSetCreateMutable(nil, 0, &callbacks)!
    for attribute in [kCTFontNameAttribute, kCTFontDownloadableAttribute, kCTFontDownloadedAttribute] {
        CFSetAddValue(required, Unmanaged.passUnretained(attribute).toOpaque())
    }
    let done = DispatchSemaphore(value: 0)
    let started = CTFontDescriptorMatchFontDescriptorsWithProgressHandler(
        [descriptor] as CFArray, required
    ) { state, progress in
        if state == .didFailWithError {
            print("CoreText font download failed: \(progress)")
            done.signal()
        }
        if state == .didFinish { done.signal() }
        return true
    }
    guard started, done.wait(timeout: .now() + 600) == .success else {
        print("CoreText font download did not complete within 600 seconds")
        exit(1)
    }
}
guard let url = readableFontURL() else {
    print("PingFangSC-Regular has no readable registered font file")
    exit(1)
}
let digest = SHA256.hash(data: try Data(contentsOf: url))
print("PingFangSC-Regular: \(url.path), \(digest)")
