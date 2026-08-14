import type { TopicCluster } from "../types/topics";

export const NOISE_LABEL = -1;

// The name to show for a cluster: the LLM's label when there is one, else
// its strongest c-TF-IDF term. Both are optional in the data -- the noise
// bucket has neither, and an LLM-grouped corpus has no statistical terms at
// all -- so there is always a third fallback rather than a blank row.
export function topicLabel(topic: TopicCluster): string {
  const llm = topic.llm_label?.trim();
  if (llm) return llm;
  const term = topic.statistical_terms.find((t) => t.trim());
  if (term) return term;
  return topic.label === NOISE_LABEL ? "Unclustered" : `topic ${topic.label}`;
}

// True when the label shown is a stand-in rather than a real name -- worth
// marking in the UI, since "Unclustered" is a bucket and not a topic.
export function isUnnamed(topic: TopicCluster): boolean {
  return !topic.llm_label?.trim() && !topic.statistical_terms.some((t) => t.trim());
}

// Noise last, then biggest cluster first: the -1 bucket is an artefact of
// the clustering, and reading it first would misrepresent the corpus.
export function sortTopics(topics: TopicCluster[]): TopicCluster[] {
  return [...topics].sort((a, b) => {
    if ((a.label === NOISE_LABEL) !== (b.label === NOISE_LABEL)) {
      return a.label === NOISE_LABEL ? 1 : -1;
    }
    return b.member_full_names.length - a.member_full_names.length;
  });
}

export function splitClassFullName(fullName: string): { pkg: string; shortName: string } {
  const segments = fullName.split(".");
  const shortName = segments.pop() ?? fullName;
  return { pkg: segments.join(".") || "(default package)", shortName };
}
