// Mirrors backend/src/flowmap/model/topic_cluster.py field-for-field.

export interface TopicCluster {
  // HDBSCAN cluster id. -1 is the noise bucket: classes that fit no
  // cluster, not a topic in its own right. It is emitted like any other
  // cluster (see topic_modelling.py's discover_topics) and carries neither
  // an LLM label nor c-TF-IDF terms, so the UI must name it itself.
  label: number;
  // ClassDocument.fullName of every class assigned to this cluster.
  member_full_names: string[];
  // c-TF-IDF top terms, computed locally. Empty when the corpus was
  // grouped by the whole-corpus LLM path instead of HDBSCAN.
  statistical_terms: string[];
  // README/markdown docs whose inferred package overlaps this cluster.
  readme_paths: string[];
  // Human-readable label from the LLM -- absent when no LLM was used.
  llm_label?: string;
}

// Mirrors backend/src/flowmap/model/topic_assignment.py. One operation can
// be assigned to more than one topic when the backend uses top-k matching.
export interface TopicAssignment {
  label: number;
  similarity: number;
}

export interface TopicOperation {
  id: string;
  label: string;
  // Full name of the entry method represented by this opseq root. This is
  // the future graph endpoint's anchor_name.
  rootMethodFullName: string;
  similarity: number;
}
