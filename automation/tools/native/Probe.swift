import Foundation

struct LocalTransport: CatalogueTransport {
    let root: URL
    var corrupt = false
    func fetch(_ url: URL, limit: Int) async throws -> Data {
        let prefix = "/catalogue/"
        precondition(url.path.hasPrefix(prefix))
        let path = String(url.path.dropFirst(prefix.count))
        precondition(!path.contains(".."))
        var data = try Data(contentsOf: root.appending(path: path))
        guard data.count <= limit else { throw CatalogueError.invalid("limite locale") }
        if corrupt && path.contains("/notices/") { data[0] ^= 1 }
        return data
    }
}

@main struct Probe {
    static func main() async throws {
        let args=CommandLine.arguments
        if args[1] == "validate" {
            let package=URL(fileURLWithPath:args[2]),trust=URL(fileURLWithPath:args[3]),work=URL(fileURLWithPath:args[4])
            let cache=try makeCache(work,trust:trust)
            let result=try await cache.update(from:URL(string:"https://catalogue-test.invalid/catalogue/")!,transport:LocalTransport(root:package))
            precondition(result.models.count>=5)
            try write(["sequence":result.sequence,"version":result.version,"notices":result.models.count,"signedAndCompatible":true,"manifestSHA256":CatalogueRules.hash(try Data(contentsOf:package.appending(path:"manifest.json")))],to:work.appending(path:"validation.json"))
            print("PASS · Catalogue signé compatible, \(result.models.count) notices, séquence \(result.sequence).")
        } else {
            let old=URL(fileURLWithPath:args[2]),new=URL(fileURLWithPath:args[3]),trust=URL(fileURLWithPath:args[4]),work=URL(fileURLWithPath:args[5])
            let dossier=URL(fileURLWithPath:args[6]),personal=URL(fileURLWithPath:args[7])
            let before=try hashes(personal);let data=try Data(contentsOf:dossier)
            let fields=try JSONDecoder().decode(IdentificationFields.self,from:data)
            let cache=try makeCache(work,trust:trust),base=URL(string:"https://catalogue-test.invalid/catalogue/")!
            let previous=try await cache.update(from:base,transport:LocalTransport(root:old))
            let oldResults=IdentificationCatalogue.search(fields,in:previous.models)
            var rejected=false
            do{_=try await cache.update(from:base,transport:LocalTransport(root:new,corrupt:true))}catch{rejected=true}
            precondition(rejected)
            let retained=await cache.current();precondition(retained.sequence==previous.sequence)
            let current=try await cache.update(from:base,transport:LocalTransport(root:new))
            let newResults=IdentificationCatalogue.search(fields,in:current.models)
            let reopened=try makeCache(work,trust:trust);let offline=await reopened.current()
            precondition(offline.sequence==current.sequence && offline.models.count==current.models.count)
            let rollback=try await cache.rollback();precondition(rollback.sequence==previous.sequence)
            let after=try hashes(personal);let dossierAfter=try Data(contentsOf:dossier)
            precondition(after==before && dossierAfter==data)
            try write(["baselineSequence":previous.sequence,"receivedSequence":current.sequence,"notices":current.models.count,"corruptionRejected":true,"offlineReopened":true,"rollbackMaintained":true,"personalFilesUnchanged":before.count,"identifierReevaluated":true,"hypothesesBefore":oldResults.map{$0.model.id.rawValue+" · "+$0.confidence.rawValue},"hypothesesAfter":newResults.map{$0.model.id.rawValue+" · "+$0.confidence.rawValue}],to:work.appending(path:"end-to-end.json"))
            print("PASS · Réception, refus de corruption, réouverture hors ligne, réévaluation Identifier et retour arrière ; \(before.count) fichiers personnels inchangés.")
        }
    }
    static func makeCache(_ work:URL,trust:URL)throws->CatalogueCache {
        let j=try JSONSerialization.jsonObject(with:Data(contentsOf:trust)) as! [String:String]
        return try CatalogueCache(root:work.appending(path:"FieldArchiveHistoricalCatalogue"),key:Data(base64Encoded:j["publicKey"]!)!,keyID:j["keyID"]!)
    }
    static func hashes(_ root:URL)throws->[String:String]{
        let files=FileManager.default.enumerator(at:root,includingPropertiesForKeys:[.isRegularFileKey])!
        var result:[String:String]=[:]
        for case let file as URL in files where try file.resourceValues(forKeys:[.isRegularFileKey]).isRegularFile==true {
            result[String(file.path.dropFirst(root.path.count+1))]=CatalogueRules.hash(try Data(contentsOf:file))
        }
        return result
    }
    static func write(_ object:[String:Any],to file:URL)throws{
        try FileManager.default.createDirectory(at:file.deletingLastPathComponent(),withIntermediateDirectories:true)
        try JSONSerialization.data(withJSONObject:object,options:[.prettyPrinted,.sortedKeys]).write(to:file)
    }
}
