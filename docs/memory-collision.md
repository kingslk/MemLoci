# 记忆对撞：设计思路

存活摘要：`.agents/decisions.md`「记忆对撞不用向量」。本文保留完整原则与伪代码。

记忆会重复，也会打架。对撞要解决的是：两条经验改过同一批文件、说的是相近的事时，判断它们到底是同一做法、互相排斥，还是只是主题接近。

不靠向量近邻。向量适合检索「和当前任务相关的记忆」，不适合判定「这两条是不是同一条经验」。对撞用更硬的证据：改过哪些文件、用了哪些词，先筛出真的撞车的对，再让模型只做关系判断。

## 原则

1. **宁可漏合，也不误合。** 碰巧都改过公共目录，不够。文件和用词必须同时撞上。
2. **先规则后模型。** 规则负责缩小候选；模型只看标题、问题和做法，判断关系，不改写正文。
3. **冲突保守。** 拿不准当成无关。明确冲突要加一次更深的复核。复核后仍然不删，只留痕迹。
4. **人工优先。** 人改过的记忆不参与合并。
5. **一对只撞一次。** 合并后两边退出本轮，避免一条经验被连续吞掉。

模型只允许给出三种结论：

```text
同一做法     措辞不同，步骤等价          → 合并
明确冲突     同一问题，约束或做法互斥    → 只记账，不删
主题接近     不同阶段或不同问题          → 跳过
拿不准       当作主题接近                → 跳过
```

「同一做法」和「明确冲突」不能同时成立。

## 流程

```text
取出未归档的记忆
  → 每条打指纹：证据文件 + 标题/问题/做法里的词
  → 两两判断是否撞车（文件重叠且用词重叠）
  → 按「文件权重大于用词」排序
  → 模型批量比较
  → 判为冲突的，单独加深复核
  → 同一做法则合成一条并退役另一条
     明确冲突则只记审计
     其余跳过
```

和「按主题收成一条经验」是两件事。主题合成解决的是同一主题下多条变更草稿；对撞解决的是：没挂同一主题、但改过同一批文件的记忆，仍然可能重复或打架。

## 伪代码

### 指纹

文件来自这条记忆绑定的证据。词来自标题、问题和可迁移步骤。中文切 2-gram，英文留下长度至少 2 的 token。

```python
def fingerprint(memory):
    files = evidence_files(memory) | changed_files_in_payload(memory)
    terms = tokenize(memory.title, memory.problem, *memory.pattern)
    return files, terms


def tokenize(*texts):
    blob = " ".join(texts).lower()
    tokens = set(re.findall(r"[a-z0-9]{2,}", blob))
    for run in re.findall(r"[\u4e00-\u9fff]+", blob):
        if len(run) <= 4:
            tokens.add(run)
        tokens.update(run[i : i + 2] for i in range(len(run) - 1))
    return tokens
```

### 撞车判定

两边文件并集、词并集上的 Jaccard 都要过线。共同改过一个很宽的目录，相似度会被并集稀释，进不了候选。

```python
MIN_SIM = 0.1


def is_collision(left_files, left_terms, right_files, right_terms):
    file_overlap = len(left_files & right_files)
    term_overlap = len(left_terms & right_terms)
    if not file_overlap or not term_overlap:
        return False
    file_sim = file_overlap / len(left_files | right_files)
    term_sim = term_overlap / len(left_terms | right_terms)
    return file_sim >= MIN_SIM and term_sim >= MIN_SIM


def collision_score(left_files, left_terms, right_files, right_terms):
    return len(left_files & right_files) * 3 + len(left_terms & right_terms)
```

### 候选对

只看可召回的记忆。人工改过的稍后跳过，不进模型。

```python
def candidate_pairs(memories):
    fps = {m.id: fingerprint(m) for m in memories}
    pairs = []
    for i, left in enumerate(memories):
        lf, lt = fps[left.id]
        for right in memories[i + 1 :]:
            rf, rt = fps[right.id]
            if not is_collision(lf, lt, rf, rt):
                continue
            pairs.append((collision_score(lf, lt, rf, rt), left, right))
    pairs.sort(key=lambda x: -x[0])
    return pairs
```

### 模型比较

默认用中等推理批量问。只有报冲突的那几对，再单独用高推理问一次。主题接近或同一做法不升级，省调用。

```python
def compare_pair(left, right, effort="medium"):
    # 只输入 title / problem / pattern
    # 输出 {same_pattern, conflict, overlap_terms, reason}
    return llm_compare(left, right, effort=effort)


def confirm_conflicts(pairs):
    results = llm_compare_batch(pairs, effort="medium")
    for i, result in enumerate(results):
        if result["conflict"]:
            results[i] = compare_pair(pairs[i][0], pairs[i][1], effort="high")
    return results
```

模型侧的判定规则可以写成：

```python
def interpret(same_pattern, conflict):
    if same_pattern and conflict:
        return "invalid"          # 不允许
    if same_pattern:
        return "merge"
    if conflict:
        return "conflict"
    return "skip"                 # 主题接近或拿不准
```

### 落库

```python
def collide(project):
    memories = [m for m in load(project) if m.status in {"active", "tentative"}]
    pairs = candidate_pairs(memories)
    comparable = [
        (left, right) for _, left, right in pairs
        if not human_corrected(left) and not human_corrected(right)
    ]
    verdicts = {
        (left.id, right.id): v
        for (left, right), v in zip(comparable, confirm_conflicts(comparable))
    }

    used = set()
    summary = {"merged": 0, "conflicts": 0, "skipped": 0}

    for _, left, right in pairs:
        if left.id in used or right.id in used:
            summary["skipped"] += 1
            continue
        if human_corrected(left) or human_corrected(right):
            summary["skipped"] += 1
            continue

        verdict = verdicts.get((left.id, right.id))
        if verdict is None:
            summary["skipped"] += 1
            continue

        if verdict["conflict"]:
            audit(left, action="conflict")   # 不改状态，不删
            summary["conflicts"] += 1
            continue

        if not verdict["same_pattern"]:
            summary["skipped"] += 1
            continue

        keep, drop = (left, right) if left.confidence >= right.confidence else (right, left)
        draft = synthesize(keep, drop)       # 收成一条可换仓库用的经验
        overwrite(keep, draft)
        retire(drop)                         # active → deprecated；tentative → candidate
        used.update([left.id, right.id])
        summary["merged"] += 1

    return summary
```

`retire` 不删除。高置信的那条留下并改写正文；另一条退役，审计里能撤回。

## 为什么这样拆

| 做法 | 不用它的原因 |
| --- | --- |
| 只用向量近邻 | 公共词、同类模块会把无关经验拉到一起 |
| 只用标题词重叠 | 标题短，误合多；也发现不了标题不同、文件相同的重复 |
| 一上来就让模型两两比 | 记忆多时组合爆炸，费用和误判都高 |
| 冲突自动删或自动合成 | 互斥的做法往往都对，只是适用条件不同；删了就丢边界 |
| 人改过的也合并 | 会把纠错过的约束冲掉 |

对撞只负责「这两条撞上了该怎么办」。检索、主题合成、人工纠错是上下游，不混进这一步。
