import Foundation
struct ArchiveSource: Codable, Identifiable, Sendable {
    let id: String
    let institution: String
    let title: String
    let locator: String
    let summary: String
    let url: URL
}

struct HistoricalModelID: RawRepresentable, Codable, Hashable, Sendable {
    let rawValue: String

    static let m65 = HistoricalModelID(rawValue: "m65-field-jacket")
}

enum ObservationField: String, Codable, CaseIterable, Identifiable, Sendable {
    case family, color, fabric, closure, pockets, hood
    case removable, label, markings, size, contract, stock, details

    var id: Self { self }

    var title: String {
        switch self {
        case .family: "Famille"
        case .color: "Couleur ou motif"
        case .fabric: "Matière apparente"
        case .closure: "Fermeture"
        case .pockets: "Nombre et disposition des poches"
        case .hood: "Capuche"
        case .removable: "Doublure et éléments amovibles"
        case .label: "Désignation inscrite"
        case .markings: "Marquages et inscriptions"
        case .size: "Taille inscrite"
        case .contract: "Numéro de contrat"
        case .stock: "Numéro de stock, FSN ou NSN"
        case .details: "Autres détails distinctifs"
        }
    }

    var choices: [String] {
        switch self {
        case .family: ["Veste de terrain", "Autre veste", "Pantalon", "Chemise", "Autre vêtement"]
        case .color: ["Vert olive", "Woodland", "Désert six couleurs", "Désert trois couleurs", "UCP", "Autre ou incertain"]
        case .closure: ["Glissière sous patte à pressions", "Glissière visible", "Boutons seuls", "Pressions seules", "Autre ou incertain"]
        case .pockets: ["Quatre poches avant", "Deux poches avant", "Deux poches de poitrine", "Autre nombre de poches avant", "Aucune poche avant"]
        case .hood: ["Logée dans le col", "Extérieure uniquement", "Absente après examen du col",
                     "Extérieure visible, col non vérifié", "Compartiment de col vide ou incomplet", "Autre ou incertaine"]
        default: []
        }
    }
}


struct IdentificationFields: Codable {
    var observations: [String: String] = [:]
    subscript(_ field: ObservationField) -> String { observations[field.rawValue, default: ""] }
}
extension String { var isBlank: Bool { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } }
