import scala.collection.mutable

def buildInterMethodCfg(
  entryPointFullName: String,
  noiseMarkers: Set[String],
  jdkPrefixes: Set[String]
): ujson.Obj = {
  val nodes = mutable.LinkedHashMap[String, ujson.Obj]()
  val edges = mutable.ArrayBuffer[ujson.Obj]()
  val visitedMethods = mutable.Set[String]()

  def addNode(id: String, obj: ujson.Obj): Unit = {
    if (!nodes.contains(id)) nodes(id) = obj
  }

  def isNoiseOrJdk(fullName: String): Boolean =
    noiseMarkers.exists(fullName.contains) || jdkPrefixes.exists(fullName.startsWith)

  def traverseMethod(methodFullName: String): Unit = {
    if (visitedMethods.contains(methodFullName)) return
    visitedMethods += methodFullName

    val methodOpt = cpg.method.fullNameExact(methodFullName).headOption
    if (methodOpt.isEmpty) return
    val method = methodOpt.get

    val entryId = s"m${method.id}"
    addNode(entryId, ujson.Obj(
      "id" -> entryId, "type" -> "entry",
      "calleeFullName" -> method.fullName, "line" -> method.lineNumber.getOrElse(-1)
    ))

    val firstCalls = method.start.repeat(_.cfgNext)(_.until(_.isCall)).isCall.l
    firstCalls.foreach { fc =>
      edges += ujson.Obj("from" -> entryId, "to" -> s"c${fc.id}", "type" -> "sequence")
    }

    method.call.l.foreach { call =>
      val callId = s"c${call.id}"
      addNode(callId, ujson.Obj(
        "id" -> callId, "type" -> "call",
        "callerMethod" -> methodFullName, "calleeFullName" -> call.methodFullName,
        "code" -> call.code, "line" -> call.lineNumber.getOrElse(-1)
      ))

      // intra-method sequence: next call(s) after this one, standard path only
      // same reasoning as above: `call` is a single node from `.foreach`, needs `.start`.
      call.start.repeat(_.cfgNext)(_.until(_.isCall)).isCall.l.foreach { nc =>
        edges += ujson.Obj("from" -> callId, "to" -> s"c${nc.id}", "type" -> "sequence")
      }
  
      call.start.repeat(_.ddgIn)(_.maxDepth(10).until(_.isCall)).isCall.dedup.l.foreach { source =>
        edges += ujson.Obj("from" -> s"c${source.id}", "to" -> callId, "type" -> "data")
      }

      // interprocedural: resolve callee(s), handling dispatch fan-out.
      // whereNot(_.isAbstract) alone misses bodyless-but-not-abstract
      // candidates -- e.g. a plain interface method has no `abstract`
      // keyword to flag, so Joern's isAbstract comes back false even
      // though there's nothing to call. block.astChildren.nonEmpty checks
      // the fact that actually matters (does this method have statements
      // to run), not the modifier; isExternal short-circuits it because
      // external methods never have an indexed body to check at all.
      val calleeMethods = call.callee.whereNot(_.isAbstract)
        .filter(m => m.isExternal || m.block.astChildren.nonEmpty).l
      if (calleeMethods.isEmpty) {
        addNode(callId + "_unresolved", ujson.Obj(
          "id" -> (callId + "_unresolved"), "type" -> "leaf", "reason" -> "unresolved"
        ))
        edges += ujson.Obj("from" -> callId, "to" -> (callId + "_unresolved"), "type" -> "invoke")
      } else {
        calleeMethods.foreach { callee =>
          val calleeEntryId = s"m${callee.id}"
          if (callee.isExternal) {
            if (isNoiseOrJdk(callee.fullName)) {
              // Structural noise (<operator>.*, <clinit>, ...) or a JDK
              // builtin -- omit entirely, not even as a leaf.
            } else {
              // A genuine external touchpoint: third-party library, or a
              // cross-language call (e.g. into TypeScript) -- keep visible.
              addNode(calleeEntryId, ujson.Obj(
                "id" -> calleeEntryId, "type" -> "leaf", "calleeFullName" -> callee.fullName
              ))
              edges += ujson.Obj("from" -> callId, "to" -> calleeEntryId, "type" -> "invoke")
            }
          } else {
            edges += ujson.Obj("from" -> callId, "to" -> calleeEntryId, "type" -> "invoke")
            traverseMethod(callee.fullName)
          }
        }
      }
    }
  }

  traverseMethod(entryPointFullName)

  ujson.Obj(
    "entryPoint" -> entryPointFullName,
    "nodes" -> nodes.values.toList,
    "edges" -> edges.toList
  )
}

val __cfgJson: String = {
  val cfgResult = buildInterMethodCfg(
    "ENTRY_POINT_FULLNAME_PLACEHOLDER",
    Set(NOISE_MARKERS_PLACEHOLDER),
    Set(JDK_PREFIXES_PLACEHOLDER)
  )
  ujson.write(cfgResult, indent = 2)
}
