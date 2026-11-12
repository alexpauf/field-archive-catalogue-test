import Foundation

enum HypothesisConfidence: String {
    case compatible = "Compatible"
    case needsConfirmation = "À confirmer"
    case unlikely = "Peu compatible"
    case undetermined = "Non déterminé"
}

struct DocumentaryCriterion: Decodable, Sendable {
    struct Verification: Decodable, Sendable {
        struct Evidence: Decodable, Sendable { let sourceID: String; let locator: String }
        let status: String
        let evidence: [Evidence]
    }
    let field: ObservationField
    let expected: String
    let sources: [String]
    let concordant: [String]
    let contradictory: [String]
    let verification: Verification?

    var citation: String { "[\(sources.joined(separator: ", "))]" }

    func matches(_ value: String) -> Bool {
        concordant.contains(comparisonValue(value))
    }

    func contradicts(_ value: String) -> Bool {
        contradictory.contains(comparisonValue(value))
    }

    private func comparisonValue(_ value: String) -> String {
        guard field == .label else { return value }
        return value.lowercased().split { !$0.isLetter && !$0.isNumber }.joined(separator: " ")
    }
}

enum HistoricalFamily: String, Decodable, CaseIterable, Identifiable, Sendable {
    case fieldJacket, utilityTop
    case outerwear, trousers, coveralls, headwear, handwear, underwear, liners, accessories, servicewear, workwear

    var id: Self { self }

    var title: String {
        switch self {
        case .fieldJacket: "Vestes de terrain"
        case .utilityTop: "Hauts utilitaires"
        case .outerwear: "Vestes et vêtements de protection"
        case .trousers: "Pantalons et shorts"
        case .coveralls: "Combinaisons et ensembles"
        case .headwear: "Coiffures et capuches"
        case .handwear: "Gants et moufles"
        case .underwear: "Sous-vêtements et bonneterie"
        case .liners: "Doublures"
        case .accessories: "Accessoires vestimentaires"
        case .servicewear: "Tenues de service"
        case .workwear: "Vêtements de travail"
        }
    }

    var category: String {
        switch self {
        case .fieldJacket: "VESTE DE TERRAIN"
        case .utilityTop: "HAUT UTILITAIRE"
        default: title.uppercased()
        }
    }
}

enum DocumentaryLevel: String, Decodable, CaseIterable, Identifiable, Sendable {
    case reference, documented, identifiable
    var id: Self { self }
    var title: String {
        switch self { case .reference: "Référence"; case .documented: "Documentée"; case .identifiable: "Identifiable" }
    }
    var explanation: String {
        switch self {
        case .reference: "Existence attestée dans une source vérifiable. Caractéristiques à documenter."
        case .documented: "Informations complémentaires sourcées. Non utilisée par Identifier."
        case .identifiable: "Caractéristiques discriminantes vérifiées. L’identification reste une hypothèse."
        }
    }
}

struct DocumentaryModel: Decodable, Identifiable, Sendable {
    struct Publication: Decodable, Sendable {
        let identitySources: [String]
        let summarySources: [String]
        let identifierEligible: Bool
        let unknowns: [String]
        let documentationLevel: DocumentaryLevel?
    }
    struct IndexMetadata: Decodable, Sendable {
        struct Attestation: Decodable, Sendable {
            let designation: String
            let sourceID: String
            let sourceURL: URL
            let documentYear: Int
            let historicalYear: Int?
            let documentSHA256: String
            let page: Int
            let quote: String
            let quoteSHA256: String
            let workID: String
        }
        let years: [Int]
        let aliases: [String]
        let attestations: [Attestation]
        let branch: String
        let variantGroup: String
        let synthetic: Bool?
    }
    struct Section: Decodable, Identifiable, Sendable {
        struct Fact: Decodable, Identifiable, Sendable {
            let label: String
            let value: String
            let sources: [String]

            var id: String { label }
            var displayLabel: String {
                let value=label.replacingOccurrences(of: " · [a-f0-9]{6}$", with: "", options: .regularExpression)
                return ["function":"Usage documenté", "aliases":"Autre désignation", "finish":"Traitement documenté"][value] ?? value
            }
            var citedValue: String { "\(value) [\(sources.joined(separator: ", "))]" }
        }

        let title: String
        let facts: [Fact]

        var id: String { title }
    }

    struct Illustration: Decodable, Sendable {
        private enum CodingKeys: String, CodingKey { case title, credit, license, url, filename, licenseURL }
        let title: String
        let credit: String
        let license: String
        let url: URL
        let filename: String?
        let licenseURL: URL?
        var catalogueRoot: URL? = nil

        var bundledURL: URL? {
            guard let filename else { return nil }
            if let catalogueRoot { return catalogueRoot.appending(path: filename) }
            return Bundle.main.url(forResource: filename, withExtension: nil, subdirectory: "Catalogue")
        }
    }

    let id: HistoricalModelID
    let name: String
    let shortName: String
    let family: HistoricalFamily
    let indexPeriod: String
    let period: String
    let intro: String
    let sections: [Section]
    var illustration: Illustration
    let sources: [ArchiveSource]
    let criteria: [DocumentaryCriterion]
    let publication: Publication?
    let indexMetadata: IndexMetadata?
    var trustedBundle = false
    private enum CodingKeys: String, CodingKey {
        case id, name, shortName, family, indexPeriod, period, intro, sections, illustration, sources, criteria, publication, indexMetadata
    }
    var documentationLevel: DocumentaryLevel {
        publication?.documentationLevel ?? ((trustedBundle || publication?.identifierEligible == true) ? .identifiable : .documented)
    }
    var identifierEligible: Bool { trustedBundle || (publication?.identifierEligible == true && documentationLevel == .identifiable) }
    var attestedYears: [Int] {
        if let years = indexMetadata?.years { return years }
        return period.split { !$0.isNumber }.compactMap { Int($0) }.filter { (1940...1999).contains($0) }
    }
    var identificationCriteria: [DocumentaryCriterion] {
        trustedBundle ? criteria : criteria.filter { $0.verification?.status == "verified" }
    }
    var citedIntro: String {
        guard let publication else { return intro }
        return "\(intro) [\(publication.summarySources.joined(separator: ", "))]"
    }
}

enum HistoricalCatalogue {
    private struct Resource: Decodable {
        let version: Int
        let models: [DocumentaryModel]
    }

    enum ResourceError: Error {
        case unsupportedVersion(Int)
    }

    static let models: [DocumentaryModel] = {
        guard let url = Bundle.main.url(forResource: "HistoricalCatalogue", withExtension: "json", subdirectory: "Catalogue") else {
            return []
        }
        return (try? load(from: url)) ?? []
    }()

    static func load(from url: URL) throws -> [DocumentaryModel] {
        let resource = try JSONDecoder().decode(Resource.self, from: Data(contentsOf: url))
        guard resource.version == 1 else { throw ResourceError.unsupportedVersion(resource.version) }
        return resource.models.map { item in var model = item; model.trustedBundle = item.publication == nil; return model }
    }

    static func model(for key: String) -> DocumentaryModel? {
        models.first { $0.id.rawValue == key }
    }
}

struct DocumentaryFinding: Identifiable {
    var id: ObservationField { criterion.field }
    let criterion: DocumentaryCriterion
    let observed: String
}

struct DocumentaryHypothesis: Identifiable {
    var id: HistoricalModelID { model.id }
    let model: DocumentaryModel
    let confidence: HypothesisConfidence
    let reason: String
    let matches: [DocumentaryFinding]
    let contradictions: [DocumentaryFinding]
    let missing: [DocumentaryFinding]
    let unresolved: [DocumentaryFinding]
}

enum IdentificationCatalogue {
    static func search(
        _ fields: IdentificationFields,
        in models: [DocumentaryModel] = HistoricalCatalogue.models
    ) -> [DocumentaryHypothesis] {
        models.filter { $0.identifierEligible && !$0.identificationCriteria.isEmpty }.map { model in
            var matches: [DocumentaryFinding] = []
            var contradictions: [DocumentaryFinding] = []
            var missing: [DocumentaryFinding] = []
            var unresolved: [DocumentaryFinding] = []
            for criterion in model.identificationCriteria {
                let value = fields[criterion.field]
                let finding = DocumentaryFinding(criterion: criterion, observed: value)
                if value.isBlank { missing.append(finding) }
                else if criterion.matches(value) { matches.append(finding) }
                else if criterion.contradicts(value) { contradictions.append(finding) }
                else { unresolved.append(finding) }
            }

            let supportingFields = Set(matches.map(\.id))
            let structure: Set<ObservationField> = [.family, .closure, .pockets, .hood]
            let confidence: HypothesisConfidence
            let reason: String
            if !contradictions.isEmpty {
                confidence = .unlikely
                reason = "Au moins un relevé contredit la configuration décrite dans la notice."
            } else if structure.isSubset(of: supportingFields)
                        || (supportingFields.contains(.label) && !supportingFields.isDisjoint(with: [.closure, .pockets, .hood])) {
                confidence = .compatible
                reason = "La configuration relevée concorde avec la notice, ou une désignation concordante est recoupée par un détail de construction. L’attribution reste une hypothèse."
            } else if !supportingFields.isDisjoint(with: structure.union([.label])) {
                confidence = .needsConfirmation
                reason = "Des indices concordent. Il manque un recoupement suffisant entre l’étiquette et la construction, ou plusieurs détails de construction."
            } else {
                confidence = .undetermined
                reason = "Les relevés ne permettent pas de proposer ce modèle. Une couleur ou une matière seule est trop générale."
            }
            return DocumentaryHypothesis(model: model, confidence: confidence, reason: reason,
                                         matches: matches, contradictions: contradictions,
                                         missing: missing, unresolved: unresolved)
        }
    }
}
