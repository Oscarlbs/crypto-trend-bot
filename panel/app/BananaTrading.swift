// BananaTrading: la app del Mac.
//
// Una ventana nativa que enseña panel/index.html y le trae los datos del servidor por SSH, con la
// clave de siempre. Solo lee: lo único que ejecuta en el servidor son dos órdenes fijas (leer la
// última foto guardada y pedirle al bot una nueva con `python -m bot.dashboard`).
//
// Se compila e instala con panel/instalar-app.sh. Opciones para probarla sin tocar nada:
//   --foto <fichero.json>    usa una foto guardada en vez de conectar con el servidor
//   --captura <fichero.png>  guarda una imagen de la pantalla cuando ya hay datos, y se cierra
import Cocoa
import WebKit

struct Fallo: Error { let texto: String }

enum Servidor {
    static let info = Bundle.main.infoDictionary ?? [:]
    static let host = info["BananaHost"] as? String ?? "usuario@tu-servidor"
    static let carpeta = info["BananaDir"] as? String ?? "/srv/apps/trendbot"

    /// Carpeta privada y de ruta corta para reutilizar la conexión SSH (macOS no admite rutas largas ahí).
    static func carpetaCompartida() -> String? {
        let ruta = "/tmp/banana-\(getuid())"
        mkdir(ruta, 0o700)
        var st = stat()
        guard lstat(ruta, &st) == 0, (st.st_mode & S_IFMT) == S_IFDIR, st.st_uid == getuid(), (st.st_mode & 0o077) == 0 else { return nil }
        return ruta
    }

    static func entreComillas(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    static func ssh(_ orden: String, segundos: TimeInterval) throws -> String {
        var args = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        if let compartida = carpetaCompartida() {
            args += ["-o", "ControlMaster=auto", "-o", "ControlPersist=300", "-o", "ControlPath=\(compartida)/%C"]
        }
        args += [host, orden]
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
        p.arguments = args
        let salida = Pipe(), errores = Pipe()
        p.standardOutput = salida
        p.standardError = errores
        p.standardInput = FileHandle.nullDevice
        do { try p.run() } catch { throw Fallo(texto: "No se pudo lanzar ssh: \(error.localizedDescription)") }

        let caduca = DispatchWorkItem { if p.isRunning { p.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + segundos, execute: caduca)
        var err = Data()
        let leeErrores = DispatchWorkItem { err = errores.fileHandleForReading.readDataToEndOfFile() }
        DispatchQueue.global().async(execute: leeErrores)
        let out = salida.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        leeErrores.wait()
        caduca.cancel()

        if p.terminationReason == .uncaughtSignal { throw Fallo(texto: "El servidor ha tardado demasiado en responder") }
        guard p.terminationStatus == 0 else {
            let ultima = String(decoding: err, as: UTF8.self).split(whereSeparator: \.isNewline).last.map(String.init)
            throw Fallo(texto: ultima ?? "No se pudo conectar con el servidor (código \(p.terminationStatus))")
        }
        return String(decoding: out, as: UTF8.self)
    }

    /// docker compose puede escribir avisos antes: la foto es la última línea que es JSON.
    static func foto(en texto: String) throws -> String {
        guard let linea = texto.split(whereSeparator: \.isNewline).last(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix("{") }),
              (try? JSONSerialization.jsonObject(with: Data(linea.utf8))) != nil
        else { throw Fallo(texto: "El servidor no ha devuelto datos") }
        return String(linea)
    }

    static func pedir(_ que: String, ficheroDePrueba: String?) throws -> String {
        if let fichero = ficheroDePrueba {
            guard let texto = try? String(contentsOfFile: fichero, encoding: .utf8) else { throw Fallo(texto: "No se puede leer \(fichero)") }
            return try foto(en: texto)
        }
        switch que {
        case "foto":
            return try foto(en: ssh("cat \(entreComillas(carpeta))/reports/dashboard.json", segundos: 25))
        case "actualizar":
            return try foto(en: ssh("cd \(entreComillas(carpeta)) && docker compose exec -T trendbot python -m bot.dashboard", segundos: 90))
        default:
            throw Fallo(texto: "Petición desconocida")
        }
    }
}

final class App: NSObject, NSApplicationDelegate, WKScriptMessageHandlerWithReply, WKNavigationDelegate {
    var ventana: NSWindow!
    var web: WKWebView!
    let cola = DispatchQueue(label: "banana.servidor")   // una sola petición al servidor a la vez
    var ficheroDePrueba: String?
    var captura: String?

    func argumento(_ nombre: String) -> String? {
        let a = CommandLine.arguments
        guard let i = a.firstIndex(of: nombre), i + 1 < a.count else { return nil }
        return a[i + 1]
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        ficheroDePrueba = argumento("--foto")
        captura = argumento("--captura")
        menus()

        let conf = WKWebViewConfiguration()
        conf.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "banana")
        web = WKWebView(frame: .zero, configuration: conf)
        web.navigationDelegate = self
        web.allowsMagnification = true
        if #available(macOS 12.0, *) { web.underPageBackgroundColor = NSColor(red: 1, green: 0.83, blue: 0.23, alpha: 1) }

        // La barra de título es del mismo amarillo que la cabecera del panel: se ve todo de una pieza
        ventana = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        ventana.title = "BananaTrading"
        ventana.titleVisibility = .hidden
        ventana.titlebarAppearsTransparent = true
        ventana.backgroundColor = NSColor(red: 1, green: 0.83, blue: 0.23, alpha: 1)
        ventana.minSize = NSSize(width: 420, height: 560)
        ventana.contentView = web
        ventana.center()
        ventana.setFrameAutosaveName("BananaTrading")
        ventana.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        guard let pagina = Bundle.main.url(forResource: "index", withExtension: "html") else {
            let aviso = NSAlert()
            aviso.messageText = "Falta el panel dentro de la app"
            aviso.informativeText = "Vuelve a instalarla con panel/instalar-app.sh"
            aviso.runModal()
            NSApp.terminate(nil)
            return
        }
        web.loadFileURL(pagina, allowingReadAccessTo: pagina.deletingLastPathComponent())
        if captura != nil { esperarYCapturar(intentos: 180) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    // El panel pide "foto" (la última guardada) o "actualizar" (una nueva); nada más
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage, replyHandler: @escaping (Any?, String?) -> Void) {
        guard let que = message.body as? String, message.frameInfo.isMainFrame else { return replyHandler(nil, "Petición no válida") }
        let prueba = ficheroDePrueba
        cola.async {
            let resultado: Result<String, Fallo>
            do { resultado = .success(try Servidor.pedir(que, ficheroDePrueba: prueba)) }
            catch let fallo as Fallo { resultado = .failure(fallo) }
            catch { resultado = .failure(Fallo(texto: error.localizedDescription)) }
            DispatchQueue.main.async {
                switch resultado {
                case .success(let json): replyHandler(json, nil)
                case .failure(let fallo): replyHandler(nil, fallo.texto)
                }
            }
        }
    }

    // La ventana solo enseña el panel: cualquier otro enlace se abre en el navegador de siempre
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { return decisionHandler(.cancel) }
        if url.isFileURL && url.deletingLastPathComponent().path == Bundle.main.resourceURL?.path { return decisionHandler(.allow) }
        if url.scheme == "https" || url.scheme == "http" { NSWorkspace.shared.open(url) }
        decisionHandler(.cancel)
    }

    @objc func refrescar(_ sender: Any?) { web.evaluateJavaScript("window.bananaRefresh && window.bananaRefresh()", completionHandler: nil) }
    @objc func acercar(_ sender: Any?) { web.pageZoom = min(2.0, web.pageZoom + 0.1) }
    @objc func alejar(_ sender: Any?) { web.pageZoom = max(0.6, web.pageZoom - 0.1) }
    @objc func tamanoReal(_ sender: Any?) { web.pageZoom = 1.0 }

    func menus() {
        let barra = NSMenu()
        func menu(_ titulo: String, _ entradas: [NSMenuItem]) {
            let raiz = NSMenuItem(), m = NSMenu(title: titulo)
            entradas.forEach { m.addItem($0) }
            raiz.submenu = m
            barra.addItem(raiz)
        }
        func entrada(_ titulo: String, _ accion: Selector?, _ tecla: String = "", _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let e = NSMenuItem(title: titulo, action: accion, keyEquivalent: tecla)
            e.keyEquivalentModifierMask = mods
            return e
        }
        menu("BananaTrading", [
            entrada("Acerca de BananaTrading", #selector(NSApplication.orderFrontStandardAboutPanel(_:))), .separator(),
            entrada("Ocultar BananaTrading", #selector(NSApplication.hide(_:)), "h"),
            entrada("Ocultar otros", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option]), .separator(),
            entrada("Salir de BananaTrading", #selector(NSApplication.terminate(_:)), "q"),
        ])
        menu("Edición", [
            entrada("Copiar", #selector(NSText.copy(_:)), "c"),
            entrada("Seleccionar todo", #selector(NSText.selectAll(_:)), "a"),
        ])
        menu("Ver", [
            entrada("Refrescar datos", #selector(refrescar(_:)), "r"), .separator(),
            entrada("Tamaño real", #selector(tamanoReal(_:)), "0"),
            entrada("Acercar", #selector(acercar(_:)), "+"),
            entrada("Alejar", #selector(alejar(_:)), "-"),
        ])
        let ventanas = [entrada("Minimizar", #selector(NSWindow.performMiniaturize(_:)), "m"),
                        entrada("Zoom", #selector(NSWindow.performZoom(_:))),
                        entrada("Cerrar", #selector(NSWindow.performClose(_:)), "w")]
        menu("Ventana", ventanas)
        NSApp.mainMenu = barra
    }

    // Solo para probar: espera a que el panel haya traído datos nuevos, guarda una imagen y sale
    func esperarYCapturar(intentos: Int) {
        guard intentos > 0 else { FileHandle.standardError.write(Data("sin datos a tiempo\n".utf8)); exit(2) }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            self.web.evaluateJavaScript("(document.querySelector('.hero') && !document.querySelector('#actualizar').disabled ? 'ok' : '') + '|' + (document.querySelector('#aviso')?.innerText || '')") { valor, _ in
                let partes = (valor as? String ?? "|").components(separatedBy: "|")
                if partes[0] != "ok" {
                    if partes.count > 1, !partes[1].isEmpty { FileHandle.standardError.write(Data((partes[1] + "\n").utf8)); exit(3) }
                    return self.esperarYCapturar(intentos: intentos - 1)
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
                    self.web.takeSnapshot(with: nil) { imagen, _ in
                        guard let tiff = imagen?.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
                              let png = rep.representation(using: .png, properties: [:]), let destino = self.captura else { exit(4) }
                        try? png.write(to: URL(fileURLWithPath: destino))
                        exit(0)
                    }
                }
            }
        }
    }
}

let app = NSApplication.shared
let delegado = App()
app.delegate = delegado
app.setActivationPolicy(.regular)
app.run()
