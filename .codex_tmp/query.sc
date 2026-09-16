importCpg("/Users/cindra/Documents/ImperialCollege/Thesis/flowmap_java/.codex_tmp/joern_cfg_output/cpg.bin")

println("=== CFG NODES ===")
cpg.method.nameExact("run").cfgNode.l.foreach { n =>
  println(s"id=${n.id} label=${n.label} code=${n.code} line=${n.lineNumber.getOrElse(-1)}")
}

println("=== COUNT++ CALL ===")
cpg.method.nameExact("run").call.codeExact("count++").l.foreach { n =>
  println(s"id=${n.id} label=${n.label} name=${n.name} methodFullName=${n.methodFullName} code=${n.code} typeFullName=${n.typeFullName} dispatchType=${n.dispatchType} line=${n.lineNumber.getOrElse(-1)}")
  println("arguments=" + n.argument.l.map(a => s"${a.argumentIndex}:${a.label}:${a.code}").mkString("[", ", ", "]"))
}

println("=== CFG EDGES ===")
cpg.method.nameExact("run").cfgNode.l.foreach { n =>
  n.cfgNext.l.foreach { m =>
    println(s"${n.id}:${n.label}:${n.code} -> ${m.id}:${m.label}:${m.code}")
  }
}

println("=== DOT CFG ===")
cpg.method.nameExact("run").dotCfg.l.foreach(println)
