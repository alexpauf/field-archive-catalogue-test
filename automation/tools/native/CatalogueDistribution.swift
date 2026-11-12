import CryptoKit
import Foundation
import ImageIO

// No dependency on SwiftData, PhotoVault, Collection, Identifier or iCloud.
// A signed v2 extension of the phase 49-51 manifest/files/notices/references format.
struct CatalogueManifest: Codable, Sendable {
    struct File: Codable, Sendable { let path: String; let bytes: Int; let sha256: String }
    let schemaVersion: Int
    let minimumReaderVersion: Int
    let catalogueVersion: String
    let contentPath: String
    let sequence: Int
    let publishedAt: Date
    let modelIDs: [String]
    let files: [File]
}

struct SignedCatalogueManifest: Codable, Sendable {
    let keyID: String
    let payload: Data
    let signature: Data
}

struct CatalogueReference: Codable, Sendable {
    let modelID: String
    let source: ArchiveSource
}

struct CatalogueSnapshot: Sendable {
    let version: String
    let sequence: Int
    let date: Date?
    let models: [DocumentaryModel]
    func model(for id: String) -> DocumentaryModel? { models.first { $0.id.rawValue == id } }
    static var embedded: Self { Self(version: "embedded-build12", sequence: 0, date: nil, models: HistoricalCatalogue.models) }
    func matching(_ query: String) -> [DocumentaryModel] {
        let words = query.split(whereSeparator: \.isWhitespace).map(String.init)
        return models.filter { model in
            let text = ([model.name, model.shortName, model.period, model.intro] + model.sections.flatMap { $0.facts.map(\.value) }).joined(separator: " ")
            return words.allSatisfy { text.localizedStandardContains($0) }
        }
    }
}

struct CatalogueSearchIndex: Sendable {
    let models: [DocumentaryModel]
    private let text: [String]
    init(models: [DocumentaryModel]) {
        self.models = models
        text = models.map { model in
            Self.fold(([model.name, model.shortName, model.period, model.intro, model.family.title, model.documentationLevel.title]
                + (model.indexMetadata?.aliases ?? []) + model.sections.flatMap { $0.facts.map(\.value) }).joined(separator: " "))
        }
    }
    private static func fold(_ value: String) -> String {
        value.folding(options: [.caseInsensitive, .diacriticInsensitive], locale: Locale(identifier: "fr_FR"))
    }
    func matching(_ query: String, family: HistoricalFamily? = nil, decade: Int? = nil, level: DocumentaryLevel? = nil) -> [DocumentaryModel] {
        let words = Self.fold(query).split(whereSeparator: \.isWhitespace).map(String.init)
        return models.indices.compactMap { i in
            let model = models[i]
            guard family == nil || model.family == family,
                  level == nil || model.documentationLevel == level,
                  decade == nil || model.attestedYears.contains(where: { $0 >= decade! && $0 < decade! + 10 }),
                  words.allSatisfy({ text[i].contains($0) }) else { return nil }
            return model
        }
    }
}

enum CatalogueError: LocalizedError {
    case invalid(String)
    var errorDescription: String? {
        switch self { case .invalid(let detail): "Catalogue refusé : \(detail). Le catalogue disponible est conservé." }
    }
}

enum CatalogueRules {
    static let maximumManifest = 512_000
    static let maximumFile = 12_000_000
    static let maximumTotal = 64_000_000
    static let maximumCache = 256_000_000
    static func hash(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
    static func require(_ condition: Bool, _ detail: String) throws {
        if !condition { throw CatalogueError.invalid(detail) }
    }
    static func identifier(_ text: String) -> Bool {
        text.range(of: "^[a-z0-9][a-z0-9-]{1,100}$", options: .regularExpression) != nil
    }
    static func validPath(_ text: String) -> Bool {
        text.range(of: "^(notices/[a-z0-9][a-z0-9-]{1,100}\\.json|images/[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}\\.(jpg|jpeg|png)|references\\.json)$", options: .regularExpression) != nil
    }
    static func https(_ url: URL) -> Bool {
        url.scheme == "https" && url.host?.isEmpty == false && url.user == nil && url.password == nil && url.query == nil && url.fragment == nil
    }
    static func documentURL(_ url: URL) -> Bool {
        url.scheme == "https" && url.host?.isEmpty == false && url.user == nil && url.password == nil
    }
    static func baseURL(_ text: String) throws -> URL {
        guard let url = URL(string: text), https(url) else { throw CatalogueError.invalid("adresse HTTPS requise") }
        return url.hasDirectoryPath ? url : url.appendingPathComponent("", isDirectory: true)
    }
    static func local(_ root: URL, _ path: String) throws -> URL {
        try require(validPath(path), "chemin non autorisé")
        let result = root.appending(path: path)
        for url in [root, result.deletingLastPathComponent(), result] {
            if let value = try? url.resourceValues(forKeys: [.isSymbolicLinkKey]), value.isSymbolicLink == true {
                throw CatalogueError.invalid("lien symbolique")
            }
        }
        return result
    }
    static func boundedRead(_ url: URL, limit: Int) throws -> Data {
        let value = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
        try require(value.isRegularFile == true && value.isSymbolicLink != true && (value.fileSize ?? Int.max) <= limit, "fichier absent ou trop volumineux")
        let data = try Data(contentsOf: url)
        try require(data.count <= limit, "taille excessive")
        return data
    }
    static func manifest(_ data: Data, key: Data, keyID: String) throws -> CatalogueManifest {
        try require(data.count <= maximumManifest, "manifeste trop volumineux")
        let envelope = try JSONDecoder().decode(SignedCatalogueManifest.self, from: data)
        let publicKey = try Curve25519.Signing.PublicKey(rawRepresentation: key)
        try require(envelope.keyID == keyID && publicKey.isValidSignature(envelope.signature, for: envelope.payload), "signature absente ou invalide")
        let m = try JSONDecoder().decode(CatalogueManifest.self, from: envelope.payload)
        try require(m.schemaVersion == 2 && (1...3).contains(m.minimumReaderVersion), "format incompatible")
        try require(m.sequence > 0 && m.sequence < Int.max && identifier(m.catalogueVersion), "version invalide")
        try require(m.contentPath.range(of: "^releases/[a-z0-9][a-z0-9-]{1,100}/$", options: .regularExpression) != nil, "répertoire de version invalide")
        try require(m.publishedAt.timeIntervalSince1970.isFinite && m.publishedAt <= Date().addingTimeInterval(86400), "date invalide")
        try require(!m.files.isEmpty && m.files.count <= 2048 && !m.modelIDs.isEmpty && m.modelIDs.count <= 1000, "lot vide ou trop volumineux")
        try require(Set(m.files.map(\.path)).count == m.files.count && Set(m.modelIDs).count == m.modelIDs.count && m.modelIDs.allSatisfy(identifier), "identifiants en double")
        var total = 0
        for file in m.files {
            try require(validPath(file.path) && file.bytes > 0 && file.bytes <= maximumFile, "métadonnées de fichier invalides")
            try require(file.sha256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil, "empreinte invalide")
            total += file.bytes
        }
        try require(total <= maximumTotal && m.files.contains { $0.path == "references.json" }, "références absentes ou taille excessive")
        return m
    }
    static func image(_ data: Data) throws {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let type = CGImageSourceGetType(source) as String?, ["public.jpeg", "public.png"].contains(type),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = props[kCGImagePropertyPixelWidth] as? Int,
              let height = props[kCGImagePropertyPixelHeight] as? Int else { throw CatalogueError.invalid("image illisible") }
        try require(width > 0 && height > 0 && width <= 8192 && height <= 8192 && width * height <= 40_000_000, "dimensions d’image excessives")
    }
    static func validateModel(_ n: DocumentaryModel) throws {
        let refs = Set(n.sources.map(\.id))
        guard let publication = n.publication else { throw CatalogueError.invalid("admission documentaire absente") }
        try require(identifier(n.id.rawValue) && !n.name.isEmpty && !n.shortName.isEmpty && !n.intro.isEmpty, "notice incomplète")
        try require(refs.count == n.sources.count && !refs.isEmpty, "références en double")
        for source in n.sources {
            try require(!source.id.isEmpty && !source.title.isEmpty && !source.institution.isEmpty && !source.locator.isEmpty && !source.summary.isEmpty && documentURL(source.url), "référence documentaire incomplète")
        }
        let minimumSources = n.documentationLevel == .identifiable ? 2 : 1
        try require(Set(publication.identitySources).count >= minimumSources && Set(publication.identitySources).isSubset(of: refs), "identité insuffisamment établie")
        if n.documentationLevel != .identifiable {
            try require(!publication.identifierEligible && n.criteria.isEmpty, "référence non admissible dans Identifier")
        } else {
            try require(publication.identifierEligible && !n.criteria.isEmpty, "admission Identifier absente")
        }
        if n.documentationLevel == .reference { try require(n.indexMetadata != nil, "attestation de référence absente") }
        if let index = n.indexMetadata {
            try require(index.synthetic != true, "données synthétiques interdites")
            try require(!index.years.isEmpty && index.years.allSatisfy { (1940...1999).contains($0) }
                        && Set(index.years).count == index.years.count && !index.aliases.isEmpty
                        && !index.branch.isEmpty && !index.variantGroup.isEmpty, "index historique incohérent")
            try require(!index.attestations.isEmpty && index.attestations.count <= 100, "attestations absentes ou excessives")
            for proof in index.attestations {
                let targetYear = proof.historicalYear ?? proof.documentYear
                let latestYear = Calendar(identifier: .gregorian).component(.year, from: Date()) + 1
                try require(refs.contains(proof.sourceID) && (1940...1999).contains(targetYear)
                            && (1800...latestYear).contains(proof.documentYear) && index.years.contains(targetYear)
                            && proof.page > 0 && proof.page <= 10000 && !proof.designation.isEmpty && !proof.workID.isEmpty
                            && index.aliases.contains(proof.designation)
                            && n.sources.contains(where: { $0.id == proof.sourceID && $0.url == proof.sourceURL }), "provenance d’attestation incohérente")
                try require(proof.documentSHA256.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil
                            && !proof.quote.isEmpty && proof.quote.utf8.count <= 2048
                            && hash(Data(proof.quote.utf8)) == proof.quoteSHA256, "preuve d’attestation invalide")
                let quotation = proof.quote.decomposedStringWithCompatibilityMapping
                    .replacingOccurrences(of: "’", with: "'").replacingOccurrences(of: "‐", with: "-").replacingOccurrences(of: "–", with: "-")
                    .replacingOccurrences(of: "(?<=[A-Za-z])-\\s*\\n\\s*(?=[a-z])", with: "", options: .regularExpression)
                    .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).replacingOccurrences(of: "*", with: "")
                try require(quotation.localizedCaseInsensitiveContains(proof.designation), "désignation absente de l’attestation")
                if let year = proof.historicalYear { try require(year <= proof.documentYear && proof.quote.contains(String(year)), "date historique absente de l’attestation") }
            }
        }
        try require(!publication.summarySources.isEmpty && Set(publication.summarySources).isSubset(of: refs), "résumé non sourcé")
        try require(Set(publication.unknowns).count == publication.unknowns.count && publication.unknowns.allSatisfy { !$0.isEmpty }, "informations inconnues incohérentes")
        try require(Set(n.sections.map(\.title)).count == n.sections.count, "sections en double")
        for section in n.sections {
            try require(!section.title.isEmpty && Set(section.facts.map(\.label)).count == section.facts.count, "champs en double")
            for fact in section.facts {
                try require(!fact.label.isEmpty && !fact.value.isEmpty && !fact.sources.isEmpty && Set(fact.sources).isSubset(of: refs), "affirmation non sourcée")
            }
        }
        try require(Set(n.criteria.map(\.field)).count == n.criteria.count, "critères en double")
        if !publication.identifierEligible { try require(n.criteria.isEmpty && !publication.unknowns.isEmpty, "notice partielle ou critères non admissibles") }
        for criterion in n.criteria {
            guard let v = criterion.verification else { throw CatalogueError.invalid("critère sans vérification explicite") }
            try require(v.status == "verified" && !criterion.sources.isEmpty && Set(criterion.sources).isSubset(of: refs), "critère non vérifié")
            try require(Set(v.evidence.map(\.sourceID)) == Set(criterion.sources) && v.evidence.allSatisfy { !$0.locator.isEmpty }, "preuve de critère absente")
            try require(!criterion.expected.isEmpty && !criterion.concordant.isEmpty && Set(criterion.concordant).isDisjoint(with: criterion.contradictory), "critère contradictoire")
            if !criterion.field.choices.isEmpty {
                try require(Set(criterion.concordant + criterion.contradictory).isSubset(of: Set(criterion.field.choices)), "observation inconnue")
            }
        }
        try require(documentURL(n.illustration.url), "référence d’illustration invalide")
        if n.illustration.filename != nil {
            try require(!n.illustration.credit.isEmpty && !n.illustration.license.isEmpty && n.illustration.license != "Sans objet", "droits photographiques absents")
            if let url = n.illustration.licenseURL { try require(documentURL(url), "licence invalide") }
        }
    }
}

protocol CatalogueTransport: Sendable {
    func fetch(_ url: URL, limit: Int) async throws -> Data
}

private final class CatalogueNoRedirect: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

struct CatalogueHTTPS: CatalogueTransport {
    private let configuration: URLSessionConfiguration
    init(configuration: URLSessionConfiguration = .ephemeral) { self.configuration = configuration }
    func fetch(_ url: URL, limit: Int) async throws -> Data {
        try CatalogueRules.require(CatalogueRules.https(url), "HTTPS requis")
        let config = configuration.copy() as! URLSessionConfiguration
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.urlCache = nil
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 60
        let session = URLSession(configuration: config, delegate: CatalogueNoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (bytes, response) = try await session.bytes(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else { throw CatalogueError.invalid("réponse du serveur") }
        try CatalogueRules.require(response.expectedContentLength <= Int64(limit), "taille réseau excessive")
        var data = Data()
        for try await byte in bytes {
            try Task.checkCancellation()
            guard data.count < limit else { throw CatalogueError.invalid("taille réseau excessive") }
            data.append(byte)
        }
        return data
    }
}

actor CatalogueCache {
    struct Pointer: Codable { var current = "embedded"; var previous: String?; var highestSequence = 0; var activatedAt: Date? }
    private let root: URL
    private let key: Data
    private let keyID: String
    private let bundle: CatalogueSnapshot
    private var pointer: Pointer
    private var updating = false
    private var pinnedVersions: [String:Int] = [:]
    private(set) var canRollback = false

    init(root: URL, key: Data, keyID: String, bundle: CatalogueSnapshot = .embedded) throws {
        try CatalogueRules.require(root.lastPathComponent == "FieldArchiveHistoricalCatalogue" && root.isFileURL, "emplacement de cache interdit")
        try CatalogueRules.require((try? root.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true && key.count == 32, "cache ou clé invalide")
        // System ancestors such as /var and /tmp are aliases on Apple platforms.
        // The leaf is fixed by application code, never by remote content.
        self.root = root.resolvingSymlinksInPath(); self.key = key; self.keyID = keyID; self.bundle = bundle
        self.pointer = (try? JSONDecoder().decode(Pointer.self, from: CatalogueRules.boundedRead(root.appending(path: "active.json"), limit: 4096))) ?? Pointer()
    }

    private func directory(_ id: String) throws -> URL {
        try CatalogueRules.require(id.range(of: "^v[0-9]+-[a-f0-9]{20}$", options: .regularExpression) != nil, "version locale invalide")
        let dir = root.appending(path: "versions/\(id)", directoryHint: .isDirectory)
        for path in [root.appending(path: "versions"), dir] {
            try CatalogueRules.require((try? path.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true, "version symbolique")
        }
        return dir
    }
    private func package(at dir: URL) throws -> CatalogueSnapshot {
        let signed = try CatalogueRules.boundedRead(dir.appending(path: "manifest.json"), limit: CatalogueRules.maximumManifest)
        let m = try CatalogueRules.manifest(signed, key: key, keyID: keyID)
        var models: [DocumentaryModel] = []
        var references: [CatalogueReference] = []
        var images: Set<String> = []
        for file in m.files {
            let data = try CatalogueRules.boundedRead(CatalogueRules.local(dir, file.path), limit: file.bytes)
            try CatalogueRules.require(data.count == file.bytes && CatalogueRules.hash(data) == file.sha256, "fichier corrompu")
            if file.path == "references.json" { references = try JSONDecoder().decode([CatalogueReference].self, from: data) }
            else if file.path.hasPrefix("notices/") {
                var model = try JSONDecoder().decode(DocumentaryModel.self, from: data)
                try CatalogueRules.require(file.path == "notices/\(model.id.rawValue).json", "nom de notice incohérent")
                try CatalogueRules.validateModel(model)
                model.illustration.catalogueRoot = dir
                models.append(model)
            } else { try CatalogueRules.image(data); images.insert(file.path) }
        }
        try CatalogueRules.require(Set(models.map { $0.id.rawValue }) == Set(m.modelIDs) && models.count == m.modelIDs.count, "liste de notices incohérente")
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        var registry: [String: Data] = [:]
        for ref in references {
            let id = ref.modelID + "#" + ref.source.id
            try CatalogueRules.require(registry[id] == nil, "référence en double")
            registry[id] = try encoder.encode(ref.source)
        }
        var needed: Set<String> = []; var usedImages: Set<String> = []
        for model in models {
            for source in model.sources {
                let id = model.id.rawValue + "#" + source.id
                try CatalogueRules.require(registry[id] == encoder.encode(source), "référence centrale manquante ou divergente")
                needed.insert(id)
            }
            if let name = model.illustration.filename {
                try CatalogueRules.require(images.contains(name), "photographie absente")
                usedImages.insert(name)
            }
        }
        try CatalogueRules.require(needed == Set(registry.keys) && usedImages == images, "contenu orphelin")
        let overrides = Set(models.map { $0.id.rawValue })
        let merged = bundle.models.filter { !overrides.contains($0.id.rawValue) } + models
        return CatalogueSnapshot(version: m.catalogueVersion, sequence: m.sequence, date: m.publishedAt, models: merged)
    }
    func current() -> CatalogueSnapshot {
        canRollback = pointer.previous != nil
        if pointer.current == "embedded" { return bundle }
        if let current = try? package(at: directory(pointer.current)) {
            return CatalogueSnapshot(version: current.version, sequence: current.sequence, date: pointer.activatedAt ?? current.date, models: current.models)
        }
        if let previous = pointer.previous {
            if previous == "embedded" {
                try? commit(Pointer(current: "embedded", previous: nil, highestSequence: pointer.highestSequence, activatedAt: nil))
                return bundle
            }
            if let valid = try? package(at: directory(previous)) {
                try? commit(Pointer(current: previous, previous: "embedded", highestSequence: pointer.highestSequence, activatedAt: Date()))
                return valid
            }
        }
        return bundle
    }
    private func commit(_ value: Pointer) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try JSONEncoder().encode(value).write(to: root.appending(path: "active.json"), options: .atomic)
        pointer = value; canRollback = value.previous != nil
    }
    func pin(_ url: URL) throws {
        let id=url.lastPathComponent
        let expected=try directory(id)
        try CatalogueRules.require(url.isFileURL && expected.standardizedFileURL.path == url.standardizedFileURL.path, "version à conserver invalide")
        pinnedVersions[id, default:0] += 1
    }
    func unpin(_ url: URL) {
        let id=url.lastPathComponent
        if let count=pinnedVersions[id],count>1 { pinnedVersions[id]=count-1 }
        else { pinnedVersions.removeValue(forKey:id) }
        if !updating { try? pruneCatalogue(removeStaging:false) }
    }
    private func pruneCatalogue(removeStaging: Bool) throws {
        // Only this catalogue cache is bounded. Public release history remains immutable on GitHub.
        let fm=FileManager.default
        let versions=root.appending(path:"versions",directoryHint:.isDirectory)
        try CatalogueRules.require((try? versions.resourceValues(forKeys:[.isSymbolicLinkKey]).isSymbolicLink) != true,"versions symboliques")
        let keep=Set([pointer.current,pointer.previous].compactMap{$0}).union(pinnedVersions.keys)
        for url in (try? fm.contentsOfDirectory(at:versions,includingPropertiesForKeys:[.isDirectoryKey,.isSymbolicLinkKey])) ?? [] {
            guard !keep.contains(url.lastPathComponent),let expected=try? directory(url.lastPathComponent),expected.standardizedFileURL.path==url.standardizedFileURL.path,
                  (try? url.resourceValues(forKeys:[.isDirectoryKey]).isDirectory)==true else { continue }
            try fm.removeItem(at:url)
        }
        if removeStaging {
            let staging=root.appending(path:"staging",directoryHint:.isDirectory)
            try CatalogueRules.require((try? staging.resourceValues(forKeys:[.isSymbolicLinkKey]).isSymbolicLink) != true,"zone temporaire symbolique")
            for url in (try? fm.contentsOfDirectory(at:staging,includingPropertiesForKeys:[.isDirectoryKey,.isSymbolicLinkKey])) ?? [] {
                guard UUID(uuidString:url.lastPathComponent) != nil,(try? url.resourceValues(forKeys:[.isSymbolicLinkKey]).isSymbolicLink) != true else { continue }
                try fm.removeItem(at:url)
            }
        }
    }
    func update(from base: URL, transport: any CatalogueTransport = CatalogueHTTPS()) async throws -> CatalogueSnapshot {
        try CatalogueRules.require(!updating, "vérification déjà en cours")
        updating = true; defer { updating = false }
        try CatalogueRules.require(CatalogueRules.https(base) && base.hasDirectoryPath, "source HTTPS invalide")
        let signed = try await transport.fetch(base.appending(path: "manifest.json"), limit: CatalogueRules.maximumManifest)
        let m = try CatalogueRules.manifest(signed, key: key, keyID: keyID)
        let id = "v\(m.sequence)-\(CatalogueRules.hash(signed).prefix(20))"
        // A deliberate rollback does not permit the same bad release to reinstall itself.
        if m.sequence <= pointer.highestSequence {
            if id == pointer.current || m.sequence < pointer.highestSequence { return current() }
            if let previous = pointer.previous, previous == id { return current() }
            throw CatalogueError.invalid("version déjà utilisée avec un autre contenu")
        }
        let fm = FileManager.default
        try pruneCatalogue(removeStaging:true)
        let total = (fm.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey])?.allObjects as? [URL] ?? []).reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        try CatalogueRules.require(total + m.files.reduce(0) { $0 + $1.bytes } < CatalogueRules.maximumCache, "espace réservé au catalogue atteint")
        let staging = root.appending(path: "staging", directoryHint: .isDirectory)
        try CatalogueRules.require((try? staging.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true, "zone temporaire symbolique")
        let stage = staging.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        try fm.createDirectory(at: stage, withIntermediateDirectories: true)
        for file in m.files {
            try Task.checkCancellation()
            var data: Data?
            for old in [pointer.current, pointer.previous].compactMap({ $0 }) where old != "embedded" {
                if let dir = try? directory(old), let local = try? CatalogueRules.local(dir, file.path),
                   let bytes = try? CatalogueRules.boundedRead(local, limit: file.bytes), bytes.count == file.bytes,
                   CatalogueRules.hash(bytes) == file.sha256 { data = bytes; break }
            }
            if data == nil { data = try await transport.fetch(base.appending(path: m.contentPath).appending(path: file.path), limit: file.bytes) }
            guard let data else { throw CatalogueError.invalid("fichier absent") }
            try CatalogueRules.require(data.count == file.bytes && CatalogueRules.hash(data) == file.sha256, "empreinte ou taille incorrecte")
            let destination = try CatalogueRules.local(stage, file.path)
            try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: destination, options: .atomic)
        }
        try signed.write(to: stage.appending(path: "manifest.json"), options: .atomic)
        _ = try package(at: stage)
        try Task.checkCancellation()
        let destination = try directory(id)
        try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !fm.fileExists(atPath: destination.path) { try fm.moveItem(at: stage, to: destination) }
        let result = try package(at: destination)
        let date = Date()
        try commit(Pointer(current: id, previous: pointer.current, highestSequence: m.sequence, activatedAt: date))
        try? pruneCatalogue(removeStaging:false)
        return CatalogueSnapshot(version: result.version, sequence: result.sequence, date: date, models: result.models)
    }
    func rollback() throws -> CatalogueSnapshot {
        try CatalogueRules.require(!updating, "vérification en cours")
        guard let previous = pointer.previous else { throw CatalogueError.invalid("aucune version précédente") }
        let snapshot = previous == "embedded" ? bundle : try package(at: directory(previous))
        try commit(Pointer(current: previous, previous: pointer.current, highestSequence: pointer.highestSequence, activatedAt: Date()))
        return snapshot
    }
}
