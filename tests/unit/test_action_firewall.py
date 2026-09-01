from packages.retrieval.firewall import ActionFirewall


def test_firewall_preserves_transferable_pattern_without_expanding_scope() -> None:
    output = ActionFirewall().compile_memory(
        {
            "id": 1,
            "title": "上传状态管理",
            "status": "active",
            "confidence": 0.9,
            "pattern": ["集中处理 progress", "重构整个 request layer"],
            "implementation": {"repository": "repo-a"},
            "do_not_copy": ["不要复制来源 Repo 目录"],
            "apply_when": ["当前任务处理上传"],
            "do_not": [],
            "evidence_ids": [10],
        },
        task="实现上传组件",
    )

    assert output["transferable_pattern"] == ["集中处理 progress"]
    assert "不要复制来源 Repo 目录" in output["do_not_copy"]
    assert any("不要因为 Memory" in item for item in output["do_not"])
    assert "request layer" not in " ".join(output["transferable_pattern"])
