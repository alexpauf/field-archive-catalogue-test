import CryptoKit
import Foundation

// Local publication tool only. Never part of the iOS target.
@main
struct Publisher {
    static func digest(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func json(_ value: Any) throws -> Data { try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .prettyPrinted, .withoutEscapingSlashes]) }
    static func main() throws {
        let args = CommandLine.arguments
        if args.count == 4 && args[1] == "keygen" {
            let privateURL = URL(fileURLWithPath: args[2]); let publicURL = URL(fileURLWithPath: args[3])
            let fm = FileManager.default
            try fm.createDirectory(at: privateURL.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let key: Curve25519.Signing.PrivateKey
            if fm.fileExists(atPath: privateURL.path) { key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(contentsOf: privateURL)) }
            else {
                key = Curve25519.Signing.PrivateKey()
                try key.rawRepresentation.write(to: privateURL, options: .withoutOverwriting)
            }
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: privateURL.path)
            let trust: [String: Any] = ["keyID": "fieldarchive-documentary-1", "publicKey": key.publicKey.rawRepresentation.base64EncodedString()]
            try json(trust).write(to: publicURL, options: .atomic)
            print("Public trust configuration written; private key kept outside the iOS project.")
            return
        }
        guard args.count == 7, args[1] == "package", let sequence = Int(args[6]), sequence > 0 else {
            fatalError("keygen PRIVATE_KEY PUBLIC_CONFIG | package INPUT_JSON IMAGE_ROOT OUTPUT PRIVATE_KEY SEQUENCE")
        }
        let input = URL(fileURLWithPath: args[2]); let images = URL(fileURLWithPath: args[3]); let output = URL(fileURLWithPath: args[4])
        let key = try Curve25519.Signing.PrivateKey(rawRepresentation: Data(contentsOf: URL(fileURLWithPath: args[5])))
        let priorManifest = output.appending(path: "manifest.json")
        if FileManager.default.fileExists(atPath: priorManifest.path) {
            let old = try JSONSerialization.jsonObject(with: Data(contentsOf: priorManifest)) as! [String: Any]
            let payload = Data(base64Encoded: old["payload"] as! String)!
            let signature = Data(base64Encoded: old["signature"] as! String)!
            guard key.publicKey.isValidSignature(signature, for: payload),
                  let previous = (try JSONSerialization.jsonObject(with: payload) as? [String: Any])?["sequence"] as? Int,
                  sequence > previous else { fatalError("Release sequence must increase; existing manifest must use the same key") }
        }
        let body = try JSONSerialization.jsonObject(with: Data(contentsOf: input)) as! [String: Any]
        let models = body["models"] as! [[String: Any]]
        var files: [[String: Any]] = []; var references: [[String: Any]] = []; var written: [String: Data] = [:]
        let fm = FileManager.default
        try fm.createDirectory(at: output, withIntermediateDirectories: true)
        func write(_ path: String, _ data: Data) throws {
            guard written[path] == nil else { return }
            written[path] = data
            files.append(["path": path, "bytes": data.count, "sha256": digest(data)])
        }
        for model in models.sorted(by: { ($0["id"] as! String) < ($1["id"] as! String) }) {
            let id = model["id"] as! String
            guard id.range(of: "^[a-z0-9][a-z0-9-]{1,100}$", options: .regularExpression) != nil else { fatalError("Unsafe model ID") }
            try write("notices/\(id).json", json(model))
            for source in model["sources"] as! [[String: Any]] { references.append(["modelID": id, "source": source]) }
            if let image = model["illustration"] as? [String: Any], let path = image["filename"] as? String {
                guard path.range(of: "^images/[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}\\.(jpg|jpeg|png)$", options: .regularExpression) != nil else { fatalError("Unsafe image path") }
                try write(path, Data(contentsOf: images.appending(path: URL(fileURLWithPath: path).lastPathComponent)))
            }
        }
        try write("references.json", json(references))
        files.sort { ($0["path"] as! String) < ($1["path"] as! String) }
        // Same file list / hashes concept as phase 49; signed exact bytes, no JSON canonicalization ambiguity on the client.
        let version = String(digest(try json(files)).prefix(20))
        let contentPath = "releases/v\(sequence)-\(version)/"
        for (path, data) in written {
            let dest = output.appending(path: contentPath).appending(path: path)
            try fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: dest, options: .withoutOverwriting)
        }
        let manifest: [String: Any] = ["schemaVersion": 2, "minimumReaderVersion": body["minimumReaderVersion"] as? Int ?? 3, "catalogueVersion": version,
                                      "sequence": sequence, "contentPath": contentPath, "publishedAt": Date().timeIntervalSinceReferenceDate,
                                      "modelIDs": models.map { $0["id"] as! String }.sorted(), "files": files]
        let payload = try json(manifest)
        let envelope: [String: Any] = ["keyID": "fieldarchive-documentary-1", "payload": payload.base64EncodedString(),
                                      "signature": try key.signature(for: payload).base64EncodedString()]
        try json(envelope).write(to: output.appending(path: "manifest.json"), options: .atomic)
        print("Prepared signed release \(sequence), \(models.count) notices. Nothing published.")
    }
}
