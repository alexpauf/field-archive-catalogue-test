import Foundation
import PDFKit
import Vision
import AppKit

@main struct PDFText {
    static func main() throws {
        let args = CommandLine.arguments
        guard args.count == 2 || args.count == 3 || args.count == 4,
              let document = PDFDocument(url: URL(fileURLWithPath: args[1])),
              !document.isEncrypted, document.pageCount <= 800 else { throw NSError(domain: "PDFText", code: 1) }
        if args.count == 3 {
            guard args[2] == "--info" else { throw NSError(domain: "PDFText", code: 7) }
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["pages": document.pageCount]))
            return
        }
        let first = args.count == 4 ? Int(args[2]) ?? 0 : 1
        let last = args.count == 4 ? Int(args[3]) ?? 0 : document.pageCount
        guard first >= 1, last >= first, last <= document.pageCount else { throw NSError(domain: "PDFText", code: 3) }
        var rows: [[String: Any]] = []
        var size = 0
        for page in (first - 1)..<last {
            guard let pdfPage = document.page(at: page) else { throw NSError(domain: "PDFText", code: 4) }
            var text = pdfPage.string ?? ""
            var method = "PDFKit"
            var regions: [[String: Any]] = []
            if text.trimmingCharacters(in: .whitespacesAndNewlines).count < 40 {
                let bounds = pdfPage.bounds(for: .mediaBox)
                let scale = min(2400 / bounds.width, 3200 / bounds.height)
                let width = Int(bounds.width * scale), height = Int(bounds.height * scale)
                guard width > 0, height > 0, let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8,
                    bytesPerRow: 0, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { throw NSError(domain: "PDFText", code: 5) }
                context.setFillColor(CGColor(gray: 1, alpha: 1)); context.fill(CGRect(x: 0, y: 0, width: width, height: height))
                context.scaleBy(x: scale, y: scale); pdfPage.draw(with: .mediaBox, to: context)
                guard let image = context.makeImage() else { throw NSError(domain: "PDFText", code: 6) }
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate
                request.recognitionLanguages = ["en-US"]
                request.usesLanguageCorrection = false
                try VNImageRequestHandler(cgImage: image).perform([request])
                let observations = (request.results ?? []).sorted { a, b in
                    abs(a.boundingBox.midY - b.boundingBox.midY) > 0.006 ? a.boundingBox.midY > b.boundingBox.midY : a.boundingBox.minX < b.boundingBox.minX
                }
                regions = observations.compactMap { item in
                    guard let value = item.topCandidates(1).first else { return nil }
                    return ["text": value.string, "confidence": value.confidence, "x": item.boundingBox.minX, "y": item.boundingBox.minY, "w": item.boundingBox.width, "h": item.boundingBox.height]
                }
                text = regions.compactMap { $0["text"] as? String }.joined(separator: "\n")
                method = "Vision-accurate-en-US"
            }
            size += text.utf8.count
            guard size <= 4_000_000 else { throw NSError(domain: "PDFText", code: 2) }
            rows.append(["page": page + 1, "text": text, "method": method, "regions": regions])
        }
        FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys]))
    }
}
