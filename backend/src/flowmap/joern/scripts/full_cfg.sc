import scala.collection.mutable


def buildFullCodebaseCfg(): ujson.Obj = {
  val nodes = mutable.LinkedHashMap[String, ujson.Obj]()
  val edges = mutable.ArrayBuffer[ujson.Obj]()
  val branchGroups = mutable.ArrayBuffer[ujson.Obj]()
  val loopGroups = mutable.ArrayBuffer[ujson.Obj]()
  val semanticFeatures = ujson.Obj()

  // (groupId, armLabel) pairs per call id.
  type ArmTags = mutable.Map[Long, mutable.ArrayBuffer[(String, String)]]
  type LoopTags = mutable.Map[Long, mutable.ArrayBuffer[String]]

  def addNode(id: String, obj: ujson.Obj): Unit = {
    if (!nodes.contains(id)) nodes(id) = obj
  }

  def stringArray(values: Iterable[String]): ujson.Arr =
    ujson.Arr(values.toList.distinct.map(ujson.Str(_))*)

  def expressionTypes(expression: Expression): List[String] = (
    // Prefer the type of the expression itself. For `System.out`, walking
    // identifiers first yields java.lang.System rather than the field-access
    // expression's actual java.io.PrintStream type.
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
    // Every field identifier in the callee body, minus the ones proven to be
    // assignment targets.
    val calleeFields = calleeEntries.flatMap(
      callee => callee.ast.isFieldIdentifier.canonicalName.l
    ).distinct.filterNot(writtenFields.contains)

    val dataSources = call.start.repeat(_.ddgIn)(_.maxDepth(10).until(_.isCall))
      .isCall.dedup.l.map(source => s"c${source.id}")
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
      // "No receiver at all" is an observation (a static or operator call);
      // "a receiver we could not render" is not.
      (if (receiver.isEmpty || receiverCode.isDefined) List("receiver") else Nil) ++
      (if (argumentsObserved) List("arguments", "inputs") else Nil) ++
      List("callsiteFields", "ddg") ++
      (if (outputType.isDefined) List("output") else Nil) ++
      (if (calleeEntries.nonEmpty) List("calleeFields") else Nil)

    val result = ujson.Obj(
      "arguments" -> stringArray(argumentCodes),
      "argumentTypes" -> stringArray(argumentTypes),
      "inputIdentifiers" -> stringArray(identifiers),
      "fieldsRead" -> stringArray((argumentFields ++ calleeFields).distinct),
      "fieldsWritten" -> stringArray(writtenFields),
      "dataSourceIds" -> stringArray(dataSources),
      "domainTypes" -> stringArray(domainTypes),
      "methodTerms" -> stringArray(methodTerms),
      "observedFeatures" -> stringArray(observed)
    )
    receiverCode.foreach(code => result("receiver") = ujson.Str(code))
    receiverType.foreach(t => result("receiverType") = ujson.Str(t))
    outputType.foreach(t => result("outputType") = ujson.Str(t))
    result
  }

  // Static initializers can carry Joern's "<empty>" method filename even
  // though their owning type declaration has the real source location.
  def methodSourceFile(method: Method): String = {
    val direct = method.filename
    if (direct.nonEmpty && direct != "<empty>") direct
    else method.typeDecl.filename.headOption.getOrElse(direct)
  }

  // Java lambdas are emitted as synthetic internal METHOD nodes, but the
  // functional-interface call that executes one does not expose that method
  // through call.callee.  The implementation is instead named by a METHOD_REF
  // argument (for example, values.forEach(<lambda>)).  Resolve those references
  // here so the synthetic method participates in the enclosing operation's
  // inter-method flow rather than being misclassified as a codebase root.
  val internalMethodsByFullName: Map[String, List[Method]] =
    cpg.method.isExternal(false).whereNot(_.isAbstract).l.groupBy(_.fullName)

  def isLambdaImplementation(method: Method): Boolean =
    method.name.matches("(?:lambda\\$.*\\$\\d+|<lambda>\\d+)")

  def referencedInternalMethods(call: Call): List[Method] =
    call.argument.isMethodRef.l.flatMap { ref =>
      internalMethodsByFullName.getOrElse(ref.methodFullName, Nil)
    }.filter(isLambdaImplementation).distinctBy(_.id)

  // Distinguishes an explicit `return` from an implicit end-of-method,
  // for a call that has NO further call ahead of it. 
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
  def armTerminus(armRoot: AstNode, armCalls: List[Call]): String = {
    if (armCalls.exists(_.methodFullName == "<operator>.throw")) "throw"
    else if (armRoot.ast.collectAll[Return].nonEmpty) "return"
    else "continues"
  }

  // Create a branch arm object based on certain branch entry point/
  // arm root
  def addArm(
    groupId: String, label: String, conditionCode: Option[String],
    armRoot: AstNode, arms: ujson.Arr, armTags: ArmTags
  ): Unit = {
    val armCalls = armRoot.ast.isCall.l
    armCalls.foreach { c =>
      armTags.getOrElseUpdate(c.id, mutable.ArrayBuffer()) += ((groupId, label))
    }
    val armObj = ujson.Obj(
      "label" -> label,
      "empty" -> ujson.Bool(armCalls.isEmpty),
      "terminus" -> armTerminus(armRoot, armCalls)
    )
    conditionCode.foreach { cc => armObj("conditionCode") = ujson.Str(cc) }
    armCalls.headOption.foreach { c => armObj("firstCallId") = ujson.Str(s"c${c.id}") }
    arms.arr.addOne(armObj)
  }

  // Add explicit empty arm for `if` with no `else`.
  def addImplicitElse(arms: ujson.Arr): Unit = {
    arms.arr.addOne(ujson.Obj(
      "label" -> "else",
      "empty" -> ujson.Bool(true),
      "terminus" -> ujson.Str("continues")
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
        addArm(groupId, label, current.condition.headOption.map(_.code), thenRoot, arms, armTags)
      }
      elseChainNext(current) match {
        case Some(next) =>
          current = next
          idx += 1
        case None =>
          val chainEndsWithElse = armRoots.size > 1
          if (chainEndsWithElse) {
            addArm(groupId, "else", None, armRoots.last, arms, armTags)
          } else {
            addImplicitElse(arms)
          }
          walking = false
      }
    }
    // Usually filter_noise_cfg can recover the visible fork from the first
    // calls in the arms. A guard containing only `return`, however, has no
    // call in either arm. Prefer the last non-operator call evaluated by the
    // condition itself: `if (user.hasRole(...)) return` must fork at hasRole,
    // not at an unrelated call before the IF. For an operator-only condition
    // such as `if (!authenticated) return`, retain the existing predecessor
    // fallback; the operator node is filtered later and offers no visible
    // anchor of its own.
    val visibleConditionCalls = head.condition.headOption.toList.flatMap(
      condition => condition.start.ast.isCall.l
        .filterNot(_.methodFullName.startsWith("<operator>."))
    )
    val precedingCalls = head.condition.headOption.toList.flatMap(
      condition => condition.start.repeat(_.cfgPrev)(_.until(_.isCall)).isCall.l
    ).distinctBy(_.id)
    val branchPointIds =
      if (visibleConditionCalls.nonEmpty) List(s"c${visibleConditionCalls.last.id}")
      else if (precedingCalls.nonEmpty) precedingCalls.map(c => s"c${c.id}")
      else List(entryId)

    branchGroups += ujson.Obj(
      "id" -> groupId, "kind" -> "IF",
      "method" -> methodFullName,
      "line" -> head.lineNumber.getOrElse(-1),
      "branchPointIds" -> stringArray(branchPointIds),
      "arms" -> arms
    )
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
        // The Java frontend does not emit the catch declaration itself
        // beneath the CATCH node. Usages of its variable are identifiers
        // with the correct type, though, and unlike ordinary parameters/
        // locals their name is absent from the method declaration lists.
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
      "terminus" -> ujson.Str("continues")
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

    val nextCallsById: Map[Long, List[Call]] =
      methodCalls.map(c => c.id -> c.start.repeat(_.cfgNext)(_.until(_.isCall)).isCall.l).toMap

    val terminusById: Map[Long, String] = methodCalls.flatMap { c =>
      val nextCalls = nextCallsById(c.id)
      val value =
        if (nextCalls.nonEmpty && nextCalls.forall(_.methodFullName == "<operator>.throw")) Some("throw")
        else if (nextCalls.isEmpty) Some(classifyTerminus(c.start.cfgNext.l))
        else None
      value.map(v => c.id -> v)
    }.toMap

    // Branch-group capture: runs before the node pass so every call in
    // an arm can be tagged when its node is built. Chain heads only --
    // an IF that is some other IF's else-chain continuation is folded
    // into that chain's group instead of getting one of its own.
    val armTags: ArmTags = mutable.Map()
    val loopTags: LoopTags = mutable.Map()
    val controlStructures = method.controlStructure.l
    val ifs = controlStructures.filter(_.controlStructureType == "IF")
    val chainedIfIds = ifs.flatMap(cs => elseChainNext(cs).map(_.id)).toSet

    // Each group records its owning method: a group is method-level
    // metadata with no node of its own, so nothing else identifies where
    // it lives once the graph is sliced (a group whose every arm is empty
    // has no tagged node to recover it from either).
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

    // first call(s) reached from method entry, skipping non-call nodes
    method.start.repeat(_.cfgNext)(_.until(_.isCall)).isCall.l.foreach { fc =>
      edges += ujson.Obj("from" -> entryId, "to" -> s"c${fc.id}", "type" -> "sequence")
    }

    methodCalls.foreach { call =>
      val callId = s"c${call.id}"

      // intra-method sequence: next call(s) after this one
      nextCallsById(call.id).foreach { nc =>
        edges += ujson.Obj("from" -> callId, "to" -> s"c${nc.id}", "type" -> "sequence")
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

      // data dependency chain -- same ddgIn walk as inter_cfg.sc, unfiltered
      call.start.repeat(_.ddgIn)(_.maxDepth(10).until(_.isCall)).isCall.dedup.l.foreach { src =>
        edges += ujson.Obj("from" -> s"c${src.id}", "to" -> callId, "type" -> "data")
      }

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

      // METHOD_REF targets are additional callees, not alternatives to the
      // ordinary resolved target above: forEach itself may resolve to an
      // external method while its lambda implementation is internal.
      referencedInternalMethods(call).foreach { implementation =>
        edges += ujson.Obj(
          "from" -> callId,
          "to" -> s"m${implementation.id}",
          "type" -> "invoke"
        )
      }
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
