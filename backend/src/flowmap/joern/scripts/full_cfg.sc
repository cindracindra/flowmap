import scala.collection.mutable


def buildFullCodebaseCfg(): ujson.Obj = {
  val nodes = mutable.LinkedHashMap[String, ujson.Obj]()
  val edges = mutable.ArrayBuffer[ujson.Obj]()
  val branchGroups = mutable.ArrayBuffer[ujson.Obj]()
  // IF arm-entry edges that the call-only CFG projection must retain.  They
  // are merged after ordinary sequence extraction so an edge already found
  // by Joern is not duplicated.
  val branchEntryEdges = mutable.ArrayBuffer[(String, String)]()
  val loopGroups = mutable.ArrayBuffer[ujson.Obj]()
  val semanticFeatures = ujson.Obj()

  // (groupId, armLabel) pairs per retained CFG-node id. Calls use these for
  // operation membership; RETURN/throw exits use them as authoritative route
  // requirements even when an arm contains no calls.
  type ArmTags = mutable.Map[Long, mutable.ArrayBuffer[(String, String)]]
  type LoopTags = mutable.Map[Long, mutable.ArrayBuffer[String]]

  def addNode(id: String, obj: ujson.Obj): Unit = {
    if (!nodes.contains(id)) nodes(id) = obj
  }

  // --- SECTION: SEMANTIC FEATURE EXTRACTION ---

  def stringArray(values: Iterable[String]): ujson.Arr =
    ujson.Arr(values.toList.distinct.map(ujson.Str(_))*)

  // Extracts candidate Java types from a receiver or argument expression for 
  // semanticFeature
  def expressionTypes(expression: Expression): List[String] = (
    expression.start.isCall.typeFullName.l ++
      expression.start.ast.isIdentifier.typeFullName.l ++
      expression.start.ast.isLiteral.typeFullName.l ++
      expression.start.ast.isCall.typeFullName.l
  ).filter(_.nonEmpty).distinct

  // Best-effort semantic evidence for one call site.
  def semanticFeature(call: Call): ujson.Obj = {
    def readable(code: String): Boolean = code != null && code.nonEmpty && code != "<empty>"
    def meaningfulName(name: String): Boolean =
      name.nonEmpty && !name.startsWith("$") && name != "this" && name != "super"
    def usefulType(t: String): Boolean =
      t.nonEmpty && t != "<empty>" && t != "ANY"

    val orderedArguments = call.argument.l.sortBy(_.argumentIndex)
    val receiver = orderedArguments.find(_.argumentIndex == 0)
    val explicitArguments = orderedArguments.filter(_.argumentIndex > 0)
    val argumentCodes = explicitArguments.map(_.code).filter(readable)
    val argumentsObserved = explicitArguments.forall(argument => readable(argument.code))
    val identifiers = explicitArguments.flatMap(
      argument => argument.start.ast.isIdentifier.name.l
    ).filter(meaningfulName).distinct
    val argumentFields = explicitArguments.flatMap(
      argument => argument.start.ast.isFieldIdentifier.canonicalName.l
    ).distinct

    val calleeEntries = call.callee.internal.l
    val assignmentNames = Set(
      "<operator>.assignment",
      "<operator>.assignmentPlus",
      "<operator>.assignmentMinus",
      "<operator>.assignmentMultiplication",
      "<operator>.assignmentDivision"
    )
    val writtenFields = calleeEntries.flatMap { callee =>
      callee.ast.isCall.l
        .filter(assignment => assignmentNames.contains(assignment.name))
        .flatMap(_.argument.l.filter(_.argumentIndex == 1))
        .flatMap(lhs => lhs.start.ast.isFieldIdentifier.canonicalName.l)
    }.distinct
    val calleeFields = calleeEntries.flatMap(
      callee => callee.ast.isFieldIdentifier.canonicalName.l
    ).distinct.filterNot(writtenFields.contains)

    val receiverCode = receiver.map(_.code).filter(readable)
    val receiverType = receiver.flatMap(expressionTypes(_).headOption).filter(usefulType)
    val argumentTypes = explicitArguments.flatMap(expressionTypes).filter(usefulType)
    val outputType = Option(call.typeFullName).filter(usefulType)
    val domainTypes = (
      receiverType.toList ++ argumentTypes ++ outputType.toList
    ).filterNot(t => Set("void", "java.lang.Object").contains(t)).distinct
    val methodTerms = call.name
      .split("(?<=[a-z0-9])(?=[A-Z])|[^A-Za-z0-9]+")
      .map(_.toLowerCase).filter(_.nonEmpty).toList

    val observed =
      (if (receiver.isEmpty || receiverCode.isDefined) List("receiver") else Nil) ++
      (if (argumentsObserved) List("arguments", "inputs") else Nil) ++
      List("callsiteFields") ++
      (if (outputType.isDefined) List("output") else Nil) ++
      (if (calleeEntries.nonEmpty) List("calleeFields") else Nil)

    val result = ujson.Obj(
      "arguments" -> stringArray(argumentCodes),
      "argumentTypes" -> stringArray(argumentTypes),
      "inputIdentifiers" -> stringArray(identifiers),
      "fieldsRead" -> stringArray((argumentFields ++ calleeFields).distinct),
      "fieldsWritten" -> stringArray(writtenFields),
      "domainTypes" -> stringArray(domainTypes),
      "methodTerms" -> stringArray(methodTerms),
      "observedFeatures" -> stringArray(observed)
    )
    receiverCode.foreach(code => result("receiver") = ujson.Str(code))
    receiverType.foreach(t => result("receiverType") = ujson.Str(t))
    outputType.foreach(t => result("outputType") = ujson.Str(t))
    result
  }


  // --- SECTION: LAMBDA FUNCTION RESOLUTION ---

  // For a method with a missing filename (Joern declaring <empty>), use 
  // filename of the class owning the method if available. 
  def methodSourceFile(method: Method): String = {
    val direct = method.filename
    if (direct.nonEmpty && direct != "<empty>") direct
    else method.typeDecl.filename.headOption.getOrElse(direct)
  }

  // Return the nearest call independently on every CFG path. Reaching a call
  // stops only that path; sibling paths keep walking until they reach their
  // own first call or terminate. A global "first BFS layer containing any
  // call" loses the normal continuation whenever a shorter throw arm reaches
  // a constructor before the surviving arm reaches its next condition/call.
  def nearestCalls(
    startPoints: List[CfgNode],
    next: CfgNode => List[CfgNode]
  ): List[Call] = {
    val seen = mutable.Set[Long]()
    val found = mutable.LinkedHashMap[Long, Call]()
    val pending = mutable.Queue.from(startPoints.distinctBy(_.id))
    while (pending.nonEmpty) {
      val node = pending.dequeue()
      if (!seen.contains(node.id)) {
        seen += node.id
        node match {
          case call: Call => found.getOrElseUpdate(call.id, call)
          case _ => next(node).foreach(pending.enqueue(_))
        }
      }
    }
    found.values.toList
  }

  // List all internal methods for purpose of resolving lambdas. 
  val internalMethodsByFullName: Map[String, List[Method]] =
    cpg.method.isExternal(false).whereNot(_.isAbstract).l.groupBy(_.fullName)

  def isLambdaImplementation(method: Method): Boolean =
    method.name.matches("(?:lambda\\$.*\\$\\d+|<lambda>\\d+)")

  // Bridge METHOD REF to actual lambda method implementation
  def referencedInternalMethods(call: Call): List[Method] =
    call.argument.isMethodRef.l.flatMap { ref =>
      internalMethodsByFullName.getOrElse(ref.methodFullName, Nil)
    }.filter(isLambdaImplementation).distinctBy(_.id)


  // --- SECTION: CREATE BRANCH GROUP AND DETAILS ---

  // Distinguishes an explicit `return` from an implicit end-of-method,
  // for a call that has NO further call ahead of it. 
  // LEGACY IMPLEMENTATION >> USE EXIT INFO INSTEAD
  def classifyTerminus(startPoints: List[CfgNode]): String = {
    val seen = mutable.Set[Long]()
    var frontier = startPoints
    while (frontier.nonEmpty) {
      val n = frontier.head
      frontier = frontier.tail
      if (!seen.contains(n.id)) {
        seen += n.id
        if (n.label == "RETURN") return "return"
        frontier = frontier ++ n.start.cfgNext.l
      }
    }
    "fallthrough"
  }

  // Flatten a chain of nested IFs into ONE group with N mutually-exclusive
  // arms, instead of N nested groups.
  def elseChainNext(cs: ControlStructure): Option[ControlStructure] = {
    val conditionId = cs.condition.headOption.map(_.id)
    val armRoots = cs.astChildren.l.filterNot(c => conditionId.contains(c.id))
    if (armRoots.size < 2) return None
    def asIf(n: AstNode): Option[ControlStructure] = n match {
      case c: ControlStructure if c.controlStructureType == "IF" => Some(c)
      case _                                                     => None
    }
    def statementsOf(n: AstNode): List[AstNode] = {
      val kids = n.astChildren.l
      if (kids.size == 1 && kids.head.label == "BLOCK") kids.head.astChildren.l else kids
    }
    val elseArm = armRoots.last
    asIf(elseArm).orElse {
      val statements = statementsOf(elseArm)
      if (statements.size == 1) asIf(statements.head) else None
    }
  }

  // Check how branch arm ends - throw, return or continues
  // LEGACY IMPLEMENTATION >> USE EXIT INFO INSTEAD
  def armTerminus(armRoot: AstNode, armCalls: List[Call]): String = {
    if (armCalls.exists(_.methodFullName == "<operator>.throw")) "throw"
    else if (armRoot.ast.collectAll[Return].nonEmpty) "return"
    else "continues"
  }

  // Path-level arm outcomes. `terminus` remains a conservative compatibility
  // summary; these exits retain the concrete RETURN/throw route and its
  // nearest call frontier for filtering and flattening to resolve later.
  def armExits(armRoot: AstNode, armCalls: List[Call]): ujson.Arr = {
    val exits = ujson.Arr()
    val armCallIds = armCalls.map(_.id).toSet
    val armAstNodes = armRoot.ast.l
    val armAstIds = armAstNodes.map(_.id).toSet

    armRoot.ast.collectAll[Return].l.foreach { ret =>
      val frontiers = ret.start.repeat(_.cfgPrev)(_.until(_.isCall)).isCall.l
        .filter(call => armCallIds.contains(call.id))
        .map(call => s"c${call.id}")
      exits.arr.addOne(ujson.Obj(
        "kind" -> ujson.Str("return"),
        "frontierIds" -> stringArray(frontiers)
      ))
    }

    armCalls.filter(_.methodFullName == "<operator>.throw").foreach { call =>
      exits.arr.addOne(ujson.Obj(
        "kind" -> ujson.Str("throw"),
        "frontierIds" -> stringArray(List(s"c${call.id}"))
      ))
    }

    // A mixed arm can have terminal paths and a normal path. Find CFG actual 
    // boundary crossings and store its nearest call node as continuingFrontiers.
    val continuingBoundaries = armAstNodes.collect { case node: CfgNode => node }
      .filter { node =>
        val isReturn = node.label == "RETURN"
        val isThrow = node match {
          case call: Call => call.methodFullName == "<operator>.throw"
          case _          => false
        }
        !isReturn && !isThrow && node.start.cfgNext.l.exists(next => !armAstIds.contains(next.id))
      }
    val continuingFrontiers = continuingBoundaries.flatMap {
      case call: Call if armCallIds.contains(call.id) => List(s"c${call.id}")
      case node => node.start.repeat(_.cfgPrev)(_.until(_.isCall)).isCall.l
        .filter(call => armCallIds.contains(call.id))
        .map(call => s"c${call.id}")
    }.distinct
    if (continuingBoundaries.nonEmpty || exits.arr.isEmpty) {
      exits.arr.addOne(ujson.Obj(
        "kind" -> ujson.Str("continues"),
        "frontierIds" -> stringArray(continuingFrontiers)
      ))
    }
    exits
  }

  // Create a branch arm object based on certain branch entry point/ arm root
  def addArm(
    groupId: String, label: String, conditionCode: Option[String],
    armRoot: AstNode, arms: ujson.Arr, armTags: ArmTags
  ): List[String] = {
    val armCalls = armRoot.ast.isCall.l
    val armCallIds = armCalls.map(_.id).toSet
    // AST order is not execution order (a variable initializer's outer call
    // can precede its arguments in the AST).  An arm entry is a call whose
    // nearest preceding call is outside this arm.  There may be several when
    // the arm itself begins with a fork.
    val entryCalls = armCalls.filter { call =>
      nearestCalls(call.start.cfgPrev.l, _.start.cfgPrev.l)
        .forall(previous => !armCallIds.contains(previous.id))
    }
    val armReturns = armRoot.ast.collectAll[Return].l
    armCalls.foreach { c =>
      armTags.getOrElseUpdate(c.id, mutable.ArrayBuffer()) += ((groupId, label))
    }
    armReturns.foreach { ret =>
      armTags.getOrElseUpdate(ret.id, mutable.ArrayBuffer()) += ((groupId, label))
    }
    val exits = armExits(armRoot, armCalls)
    exits.arr.foreach { exit =>
      exit.obj("branchRequirements") = ujson.Arr(ujson.Obj(
        "groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str(label)
      ))
    }
    val armObj = ujson.Obj(
      "label" -> label,
      "empty" -> ujson.Bool(armCalls.isEmpty),
      "terminus" -> armTerminus(armRoot, armCalls),
      "exits" -> exits
    )
    conditionCode.foreach { cc => armObj("conditionCode") = ujson.Str(cc) }
    entryCalls.headOption.foreach { c => armObj("firstCallId") = ujson.Str(s"c${c.id}") }
    arms.arr.addOne(armObj)
    entryCalls.map(call => s"c${call.id}")
  }

  // Add explicit empty arm for `if` with no `else`.
  def addImplicitElse(groupId: String, arms: ujson.Arr): Unit = {
    arms.arr.addOne(ujson.Obj(
      "label" -> "else",
      "empty" -> ujson.Bool(true),
      "terminus" -> ujson.Str("continues"),
      "exits" -> ujson.Arr(ujson.Obj(
        "kind" -> ujson.Str("continues"),
        "branchRequirements" -> ujson.Arr(ujson.Obj(
          "groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str("else")
        ))
      ))
    ))
  }

  // Emits ONE group for a whole if / else-if / else chain.
  def emitIfChain(
    head: ControlStructure, methodFullName: String, entryId: String, armTags: ArmTags
  ): Unit = {
    val groupId = s"cs${head.id}"
    val arms = ujson.Arr()
    var current = head
    var idx = 0
    var walking = true
    while (walking) {
      val conditionId = current.condition.headOption.map(_.id)
      val armRoots = current.astChildren.l.filterNot(c => conditionId.contains(c.id))
      val label = if (idx == 0) "if" else s"elseif$idx"
      armRoots.headOption.foreach { thenRoot =>
        val heads = addArm(
          groupId, label, current.condition.headOption.map(_.code), thenRoot, arms, armTags
        )
        // The concrete branch point is resolved below; retain the heads for
        // now so both sides of an asymmetric IF survive call projection.
        heads.foreach(headId => branchEntryEdges += ((groupId, headId)))
      }
      elseChainNext(current) match {
        case Some(next) =>
          current = next
          idx += 1
        case None =>
          val chainEndsWithElse = armRoots.size > 1
          if (chainEndsWithElse) {
            addArm(groupId, "else", None, armRoots.last, arms, armTags)
              .foreach(headId => branchEntryEdges += ((groupId, headId)))
          } else {
            addImplicitElse(groupId, arms)
          }
          walking = false
      }
    }

    // Keep the raw condition's physical CFG exit call for route edges,
    // including operator calls. Operators are removed later by noise
    // filtering, whose edge bridge projects the fork onto surviving calls.
    // The group's public branchPointIds remain presentation-facing visible
    // anchors, so condition operators do not leak into existing consumers.
    val conditionExitCalls = head.condition.headOption.toList.flatMap { condition =>
      val conditionNodes = condition.start.ast.l.collect { case node: CfgNode => node }
      val conditionIds = conditionNodes.map(_.id).toSet
      val boundaryCalls = conditionNodes.collect { case call: Call => call }
        .filter(call => call.start.cfgNext.l.exists(next => !conditionIds.contains(next.id)))
        .distinctBy(_.id)
      if (boundaryCalls.nonEmpty) boundaryCalls
      else condition.start.ast.isCall.l.lastOption.toList
    }
    val precedingCalls = head.condition.headOption.toList.flatMap(
      condition => nearestCalls(condition.start.cfgPrev.l, _.start.cfgPrev.l)
    ).filterNot(_.methodFullName == "<operator>.throw").distinctBy(_.id)
    val visibleConditionCalls = head.condition.headOption.toList.flatMap(
      condition => condition.start.ast.isCall.l
        .filterNot(_.methodFullName.startsWith("<operator>."))
    )
    val branchPointIds =
      if (visibleConditionCalls.nonEmpty) List(s"c${visibleConditionCalls.last.id}")
      else if (precedingCalls.nonEmpty) precedingCalls.map(c => s"c${c.id}")
      else List(entryId)
    val routePointIds =
      if (conditionExitCalls.nonEmpty) conditionExitCalls.map(c => s"c${c.id}")
      else branchPointIds

    branchGroups += ujson.Obj(
      "id" -> groupId, "kind" -> "IF",
      "method" -> methodFullName,
      "line" -> head.lineNumber.getOrElse(-1),
      "branchPointIds" -> stringArray(branchPointIds),
      "arms" -> arms
    )

    // Replace the temporary group key with each physical anchor.  Deferring
    // insertion until all normal sequence edges exist avoids duplicates.
    val groupHeads = branchEntryEdges.collect {
      case (`groupId`, headId) => headId
    }.toList
    branchEntryEdges.filterInPlace(_._1 != groupId)
    routePointIds.foreach { pointId =>
      groupHeads.foreach(headId => branchEntryEdges += ((pointId, headId)))
    }
  }

  // Emits ONE group for a try's mutually exclusive outcomes. The try body
  // itself is the common spine before the fork, and finally is common flow
  // after it, so neither is an arm. 
  def emitTryGroup(cs: ControlStructure, method: Method, armTags: ArmTags): Unit = {
    val groupId = s"cs${cs.id}"
    val armRoots = cs.astChildren.l
    val arms = ujson.Arr()
    val ordinaryVariableNames = (method.parameter.name.l ++ method.local.name.l).toSet
    var catchIdx = 0
    armRoots.foreach { armRoot =>
      val structureType = armRoot match {
        case c: ControlStructure => c.controlStructureType
        case _                   => ""
      }
      if (structureType == "CATCH") {
        catchIdx += 1
        addArm(groupId, s"catch$catchIdx", None, armRoot, arms, armTags)
        val exceptionType = armRoot.ast.isIdentifier
          .filterNot(i => ordinaryVariableNames.contains(i.name))
          .map(_.typeFullName)
          .find(t => t.nonEmpty && t != "<empty>")
        exceptionType.foreach { t =>
          arms.arr.last.obj("exceptionType") = ujson.Str(t)
        }
      }
    }
    arms.arr.addOne(ujson.Obj(
      "label" -> "noCatch",
      "empty" -> ujson.Bool(true),
      "terminus" -> ujson.Str("continues"),
      "exits" -> ujson.Arr(ujson.Obj(
        "kind" -> ujson.Str("continues"),
        "branchRequirements" -> ujson.Arr(ujson.Obj(
          "groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str("noCatch")
        ))
      ))
    ))
    branchGroups += ujson.Obj(
      "id" -> groupId, "kind" -> "TRY",
      "method" -> method.fullName,
      "line" -> cs.lineNumber.getOrElse(-1),
      "arms" -> arms
    )
  }

  // Loops are repetition metadata, not mutually-exclusive branch arms.
  // Keep their source guard even when its implementation is stripped as
  // java.util/operator noise, and tag calls in the lexical BODY only.
  // Java loop bodies are represented by BLOCK children even when the
  // source omitted braces; selecting BLOCK also excludes a traditional
  // for-loop's one-time initializer and per-iteration update expressions.
  def emitLoopGroup(
    cs: ControlStructure, methodFullName: String, loopTags: LoopTags
  ): Unit = {
    val groupId = s"loop${cs.id}"
    // FOR has two BLOCK children in the Java CPG: order 1 is initializer,
    // the LAST block is the repeating body. WHILE/DO use the same final
    // block convention. Taking every block incorrectly marks `int i = 0`
    // as repeated.
    val bodyCalls = cs.astChildren.l
      .filter(_.label == "BLOCK").lastOption.toList
      .flatMap(_.ast.isCall.l)
      .groupBy(_.id).values.map(_.head).toList
    bodyCalls.foreach { c =>
      loopTags.getOrElseUpdate(c.id, mutable.ArrayBuffer()) += groupId
    }
    val rawCondition = cs.condition.headOption.map(_.code).getOrElse("").trim
    // Enhanced-for is lowered to WHILE by the Java frontend, whose
    // "condition" code is the whole source loop. Recover its source-facing
    // kind and keep only the header for the UI tooltip.
    val isSourceFor = rawCondition.startsWith("for (")
    val displayCondition =
      if (isSourceFor) rawCondition.takeWhile(_ != '{').trim
      else rawCondition
    val sourceKind =
      if (isSourceFor && displayCondition.contains(":")) "FOR_EACH"
      else if (isSourceFor) "FOR"
      else cs.controlStructureType
    val loopObj = ujson.Obj(
      "id" -> groupId,
      "kind" -> sourceKind,
      "method" -> methodFullName,
      "line" -> cs.lineNumber.getOrElse(-1)
    )
    Option(displayCondition).filter(_.nonEmpty).foreach { code =>
      loopObj("conditionCode") = ujson.Str(code)
    }
    loopGroups += loopObj
  }

  cpg.method.isExternal(false).whereNot(_.isAbstract).l.foreach { method =>
    val entryId = s"m${method.id}"
    val entryNode = ujson.Obj(
      "id" -> entryId, "type" -> "entry",
      "calleeFullName" -> method.fullName,
      "sourceFile" -> methodSourceFile(method),
      "line" -> method.lineNumber.getOrElse(-1)
    )
    if (method.name == "<init>" && method.code == "<empty>") {
      entryNode("implicitConstructor") = ujson.Bool(true)
    }
    addNode(entryId, entryNode)

    val methodCalls = method.call.l
    val explicitReturns = method.ast.collectAll[Return].l
    val methodReturn = Option(method.methodReturn)
    val returnIds = explicitReturns.map(_.id).toSet
    val fallthroughExitId = methodReturn.map(ret => s"f${ret.id}")

    explicitReturns.foreach { ret =>
      val exitId = s"r${ret.id}"
      addNode(exitId, ujson.Obj(
        "id" -> exitId, "type" -> "exit", "exitKind" -> "return",
        "callerMethod" -> method.fullName,
        "sourceFile" -> methodSourceFile(method),
        "code" -> ret.code, "line" -> ret.lineNumber.getOrElse(-1)
      ))
    }
    // Starting immediately after an active node, find method exits reached
    // before another call.
    def directExitIds(startPoints: List[CfgNode]): List[String] = {
      val found = mutable.ArrayBuffer[String]()
      val seen = mutable.Set[Long]()
      var frontier = startPoints
      if (frontier.isEmpty) fallthroughExitId.foreach(found += _)
      while (frontier.nonEmpty) {
        val node = frontier.head
        frontier = frontier.tail
        if (!seen.contains(node.id)) {
          seen += node.id
          if (returnIds.contains(node.id)) found += s"r${node.id}"
          else if (node.isInstanceOf[Call] && node.asInstanceOf[Call].methodFullName == "<operator>.throw") {
            found += s"t${node.id}"
          }
          else if (methodReturn.exists(_.id == node.id)) found += s"f${node.id}"
          else if (!node.isInstanceOf[Call]) {
            val next = node.start.cfgNext.l
            if (next.isEmpty) fallthroughExitId.foreach(found += _)
            else frontier = frontier ++ next
          }
        }
      }
      found.distinct.toList
    }

    val nextCallsById: Map[Long, List[Call]] =
      methodCalls.map(c => c.id -> nearestCalls(c.start.cfgNext.l, _.start.cfgNext.l)).toMap

    val terminusById: Map[Long, String] = methodCalls.flatMap { c =>
      val nextCalls = nextCallsById(c.id)
      val value =
        if (nextCalls.nonEmpty && nextCalls.forall(_.methodFullName == "<operator>.throw")) Some("throw")
        else if (nextCalls.isEmpty) Some(classifyTerminus(c.start.cfgNext.l))
        else None
      value.map(v => c.id -> v)
    }.toMap

    // Identify all constrol structure info: runs before the node pass so 
    // every call in an arm can be tagged when its node is built.
    val armTags: ArmTags = mutable.Map()
    val loopTags: LoopTags = mutable.Map()
    val controlStructures = method.controlStructure.l
    val ifs = controlStructures.filter(_.controlStructureType == "IF")
    val chainedIfIds = ifs.flatMap(cs => elseChainNext(cs).map(_.id)).toSet

    // Each group records its owning method.
    ifs.filterNot(cs => chainedIfIds.contains(cs.id)).foreach { head =>
      emitIfChain(head, method.fullName, entryId, armTags)
    }
    controlStructures.filter(_.controlStructureType == "TRY").foreach { cs =>
      emitTryGroup(cs, method, armTags)
    }
    controlStructures.filter(cs =>
      Set("FOR", "WHILE", "DO", "DO_WHILE").contains(cs.controlStructureType)
    ).foreach { cs =>
      emitLoopGroup(cs, method.fullName, loopTags)
    }

    // Branch capture is complete now, so explicit return nodes can carry
    // every enclosing arm (outermost through innermost). This is the only
    // reliable route evidence for a zero-call return.
    explicitReturns.foreach { ret =>
      armTags.get(ret.id).foreach { tags =>
        val tagArr = ujson.Arr()
        tags.distinct.foreach { case (groupId, label) =>
          tagArr.arr.addOne(ujson.Obj(
            "groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str(label)
          ))
        }
        nodes(s"r${ret.id}")("branchArms") = tagArr
      }
    }

    // first call(s) reached from method entry, skipping non-call nodes
    nearestCalls(method.start.cfgNext.l, _.start.cfgNext.l).foreach { fc =>
      edges += ujson.Obj("from" -> entryId, "to" -> s"c${fc.id}", "type" -> "sequence")
    }
    directExitIds(method.start.cfgNext.l).foreach { exitId =>
      edges += ujson.Obj("from" -> entryId, "to" -> exitId, "type" -> "sequence")
    }

    methodCalls.foreach { call =>
      val callId = s"c${call.id}"

      // intra-method sequence: next call(s) after this one. A throw operator
      // is terminal even if Joern's syntactic CFG exposes later statements;
      // it owns only the structural throw-exit edge emitted below.
      if (call.methodFullName != "<operator>.throw") {
        nextCallsById(call.id).foreach { nc =>
          edges += ujson.Obj("from" -> callId, "to" -> s"c${nc.id}", "type" -> "sequence")
        }
      }
      if (call.methodFullName == "<operator>.throw") {
        val throwExitId = s"t${call.id}"
        val throwExit = ujson.Obj(
          "id" -> throwExitId, "type" -> "exit", "exitKind" -> "throw",
          "callerMethod" -> method.fullName,
          "sourceFile" -> methodSourceFile(method),
          "code" -> call.code, "line" -> call.lineNumber.getOrElse(-1)
        )
        armTags.get(call.id).foreach { tags =>
          val tagArr = ujson.Arr()
          tags.distinct.foreach { case (groupId, label) =>
            tagArr.arr.addOne(ujson.Obj(
              "groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str(label)
            ))
          }
          throwExit("branchArms") = tagArr
        }
        addNode(throwExitId, throwExit)
        edges += ujson.Obj("from" -> callId, "to" -> throwExitId, "type" -> "sequence")
      } else {
        directExitIds(call.start.cfgNext.l).foreach { exitId =>
          edges += ujson.Obj("from" -> callId, "to" -> exitId, "type" -> "sequence")
        }
      }

      val callNode = ujson.Obj(
        "id" -> callId, "type" -> "call",
        "callerMethod" -> method.fullName, "calleeFullName" -> call.methodFullName,
        "sourceFile" -> methodSourceFile(method),
        "code" -> call.code, "line" -> call.lineNumber.getOrElse(-1)
      )
      armTags.get(call.id).foreach { tags =>
        val tagArr = ujson.Arr()
        tags.foreach { case (groupId, label) =>
          tagArr.arr.addOne(ujson.Obj("groupId" -> ujson.Str(groupId), "armLabel" -> ujson.Str(label)))
        }
        callNode("branchArms") = tagArr
      }
      loopTags.get(call.id).foreach { ids =>
        val loopArr = ujson.Arr()
        ids.distinct.foreach(id => loopArr.arr.addOne(ujson.Str(id)))
        callNode("loopIds") = loopArr
      }
      terminusById.get(call.id).foreach { t => callNode("terminus") = ujson.Str(t) }
      addNode(callId, callNode)
      semanticFeatures(callId) = semanticFeature(call)

      // interprocedural traversal
      val resolvedCallees = call.callee.whereNot(_.isAbstract).l
      val traversableCallees = resolvedCallees.filter(
        m => m.isExternal || m.block.astChildren.nonEmpty
      )
      if (resolvedCallees.isEmpty) {
        addNode(callId + "_unresolved", ujson.Obj(
          "id" -> (callId + "_unresolved"), "type" -> "leaf", "reason" -> "unresolved"
        ))
        edges += ujson.Obj("from" -> callId, "to" -> (callId + "_unresolved"), "type" -> "invoke")
      } else {
        traversableCallees.foreach { callee =>
          val calleeEntryId = s"m${callee.id}"
          if (callee.isExternal) {
            addNode(calleeEntryId, ujson.Obj(
              "id" -> calleeEntryId, "type" -> "leaf", "calleeFullName" -> callee.fullName
            ))
            edges += ujson.Obj("from" -> callId, "to" -> calleeEntryId, "type" -> "invoke")
          } else {
            edges += ujson.Obj("from" -> callId, "to" -> calleeEntryId, "type" -> "invoke")
          }
        }
      }

      // Bridge lambda implementation via METHOD_REF targets.
      referencedInternalMethods(call).foreach { implementation =>
        edges += ujson.Obj(
          "from" -> callId,
          "to" -> s"m${implementation.id}",
          "type" -> "invoke"
        )
      }
    }

    // Joern creates a synthetic METHOD_RETURN for every method. It represents
    // a genuine fallthrough only when the projected CFG reaches it without
    // first crossing an explicit return or throw. Add the presentation node
    // only when directExitIds emitted an edge to that endpoint.
    fallthroughExitId.foreach { exitId =>
      val isUsed = edges.exists { edge =>
        edge.obj.get("to").exists(_.str == exitId)
      }
      if (isUsed) {
        methodReturn.foreach { ret =>
          addNode(exitId, ujson.Obj(
            "id" -> exitId, "type" -> "exit", "exitKind" -> "fallthrough",
            "callerMethod" -> method.fullName,
            "sourceFile" -> methodSourceFile(method),
            "line" -> method.lineNumberEnd.getOrElse(method.lineNumber.getOrElse(-1))
          ))
        }
      }
    }
  }

  val existingSequenceEdges = edges.collect {
    case edge if edge.obj.get("type").exists(_.str == "sequence") =>
      (edge.obj("from").str, edge.obj("to").str)
  }.toSet
  branchEntryEdges.distinct.foreach { case (source, target) =>
    if (!existingSequenceEdges.contains((source, target))) {
      edges += ujson.Obj("from" -> source, "to" -> target, "type" -> "sequence")
    }
  }

  ujson.Obj(
    "nodes" -> nodes.values.toList,
    "edges" -> edges.toList,
    "branchGroups" -> branchGroups.toList,
    "loopGroups" -> loopGroups.toList,
    "semanticFeatures" -> semanticFeatures
  )
}

val __cfgJson: String = {
  val cfgResult = buildFullCodebaseCfg()
  ujson.write(cfgResult, indent = 2)
}
