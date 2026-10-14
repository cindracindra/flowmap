# Spring PetClinic demo data

Pre-computed FlowMap results for [Spring PetClinic](https://github.com/spring-projects/spring-petclinic)
(commit `500158f`), used by `./flowmap.sh --demo`.

Only the three files the viewer reads are kept:

| File | Contents |
| --- | --- |
| `topic_cluster.json` | Discovered topics and their member classes |
| `topic_operations.json` | Operations assigned to each topic |
| `graph_bundle.json` | Phase-annotated control-flow graphs for every reachable method |

Generated with:

```bash
./flowmap.sh path/to/spring-petclinic --analyse-only --provider together --preset fine
```
