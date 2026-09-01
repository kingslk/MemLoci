import pytest
from sqlalchemy import select

from packages.common.models import AgentQueryLog, AgentSession, Memory, Project, Repository
from packages.retrieval.service import RetrievalService, compact_evidence_payload


def _project_with_repos(db, name: str = "mall") -> tuple[Project, Repository, Repository]:
    project = Project(name=name)
    db.add(project)
    db.flush()
    current = Repository(
        project_id=project.id,
        name="shop-web",
        gitlab_project_id="11",
        clone_url="https://gitlab.example.com/game/shop-web.git",
    )
    other = Repository(
        project_id=project.id,
        name="shop-ios",
        gitlab_project_id="22",
        clone_url="https://gitlab.example.com/game/shop-ios.git",
    )
    db.add_all([current, other])
    db.flush()
    return project, current, other


def _memory(
    db,
    project_id: int,
    repository_id: int,
    title: str,
    problem: str,
    apply_when: list[str],
    *,
    status: str = "active",
) -> Memory:
    memory = Memory(
        project_id=project_id,
        repository_id=repository_id,
        title=title,
        status=status,
        confidence=0.65,
        problem=problem,
        pattern=[title],
        apply_when=apply_when,
    )
    db.add(memory)
    db.flush()
    return memory


def test_numeric_project_name_is_not_forced_to_missing_id(db) -> None:
    project, _, _ = _project_with_repos(db, name="2048")
    resolved = RetrievalService(db)._resolve_project("2048")
    assert resolved.id == project.id


def test_missing_numeric_project_lists_available_ids(db) -> None:
    _project_with_repos(db)
    with pytest.raises(ValueError, match="1=mall") as error:
        RetrievalService(db)._resolve_project("2048")
    assert "shop-web" in str(error.value)
    assert "GitLab" in str(error.value)


def test_unique_repo_name_infers_project_even_if_project_ref_is_wrong(db) -> None:
    project, current, _ = _project_with_repos(db)
    service = RetrievalService(db)
    for project_ref in ("2048", "", "not-a-project"):
        resolved_project, resolved_repo = service._resolve_scope(project_ref, "shop-web")
        assert resolved_project.id == project.id
        assert resolved_repo.id == current.id


def test_memory_context_accepts_repo_only(db) -> None:
    project, current, _ = _project_with_repos(db)
    context = RetrievalService(db).memory_context(
        project_ref="",
        repository_ref=current.name,
        task="查一下键盘",
    )
    assert context["project"]["id"] == project.id
    assert context["repository"]["name"] == "shop-web"


def test_keyword_score_keeps_chinese_overlap_and_drops_unrelated() -> None:
    on_topic = RetrievalService._keyword_score(
        "注销原因 textarea 键盘",
        "键盘弹起后 focus textarea，避免带动页面滚动",
    )
    off_topic = RetrievalService._keyword_score(
        "注销原因 textarea 键盘",
        "下载引导 充值回调 AppClip CDN 领取防重",
    )
    assert on_topic > 0.2
    assert on_topic > off_topic


def test_offtopic_memories_are_not_returned(db) -> None:
    project, current, other = _project_with_repos(db)
    wanted = _memory(
        db,
        project.id,
        other.id,
        "恢复滚动与输入聚焦的时序处理",
        "键盘弹起后 focus 会带动页面滚动",
        ["textarea", "键盘", "focus"],
    )
    _memory(
        db,
        project.id,
        current.id,
        "下载引导弹窗",
        "首次进入展示下载引导",
        ["下载", "引导"],
    )
    _memory(
        db,
        project.id,
        current.id,
        "充值回调去重",
        "充值结果回调需要按订单号去重",
        ["充值", "回调"],
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=str(project.id),
        repository_ref=current.name,
        task="注销原因 textarea 被键盘挡住，滚动和 focus 错乱",
        files=["src/pages/Reason.tsx"],
        symbols=["AccountOffboard", "scrollIntoView"],
        session_id="keyboard-session",
    )
    ids = [item["memory_id"] for item in context["results"]]
    assert ids == [wanted.id]


def test_active_memory_wins_over_relevant_tentative_memory(db) -> None:
    project, current, _ = _project_with_repos(db)
    active = _memory(
        db,
        project.id,
        current.id,
        "上传取消回调处理",
        "取消上传后停止进度回调",
        ["上传", "取消", "回调"],
    )
    _memory(
        db,
        project.id,
        current.id,
        "上传取消回调的新草稿",
        "取消上传后停止进度回调",
        ["上传", "取消", "回调"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="上传取消以后还有进度回调",
    )

    assert context["recall_mode"] == "active"
    assert [item["memory_id"] for item in context["results"]] == [active.id]


def test_tentative_memory_is_labeled_fallback_without_relevant_active(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "下载引导弹窗",
        "首次进入展示下载引导",
        ["下载", "引导"],
    )
    tentative = _memory(
        db,
        project.id,
        current.id,
        "上传取消回调处理",
        "取消上传后停止进度回调",
        ["上传", "取消", "回调"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="上传取消以后还有进度回调",
    )

    assert context["recall_mode"] == "tentative_fallback"
    assert [item["memory_id"] for item in context["results"]] == [tentative.id]
    assert "未经评测或人工确认" in context["results"][0]["status_notice"]


def test_index_tsx_does_not_inflate_path_score() -> None:
    score = RetrievalService._path_score(
        ["apps/packages/account/src/components/AccountOffboard/Verify/index.tsx"],
        ["Verify"],
        "提交钩子校验需跳过生成目录与删除文件",
    )
    assert score == 0.0
    on_topic = RetrievalService._path_score(
        ["apps/packages/account/src/components/AccountOffboard/Verify/index.tsx"],
        ["Verify"],
        "AccountOffboard Verify 页身份证输入",
    )
    assert on_topic > 0


def test_path_score_uses_leaf_names_not_monorepo_folders() -> None:
    needles = RetrievalService._path_needles(
        "apps/packages/account/src/components/AccountOffboard/Verify/index.tsx"
    )
    assert needles == ["verify", "accountoffboard"]
    score = RetrievalService._path_score(
        ["apps/packages/account/src/components/AccountOffboard/Verify/index.tsx"],
        [],
        "apps packages account src 构建配置",
    )
    assert score == 0.0


def test_anonymous_sessions_are_isolated_per_repo(db) -> None:
    project, current, other = _project_with_repos(db)
    service = RetrievalService(db)
    service.memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="查一下键盘",
        session_id="anonymous",
    )
    service.memory_context(
        project_ref=project.id,
        repository_ref=other.name,
        task="查一下键盘",
        session_id="anonymous",
    )
    keys = set(db.scalars(select(AgentSession.session_id)).all())
    assert keys == {
        f"anonymous:{project.id}:{current.id}",
        f"anonymous:{project.id}:{other.id}",
    }


def test_recall_settings_split_ios_terms() -> None:
    from packages.common.config import get_settings

    settings = get_settings()
    assert settings.recall_top_k == 4
    assert settings.recall_keep_hint_primary is True


def test_chinese_bigrams_keep_interior_words() -> None:
    terms = RetrievalService._terms("不是金额，是狂点支付会重复扣")
    assert {"金额", "狂点", "支付", "重复"} <= terms


def test_clarifying_turn_recalls_repeat_pay_not_amount(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "充值优惠券实付金额",
        "选券后实付对不上，0 元怎么走",
        ["金额", "优惠券", "0元"],
        status="tentative",
    )
    repeat = _memory(
        db,
        project.id,
        current.id,
        "购买弹窗连点会重复下单",
        "用户狂点支付会重复扣款",
        ["连点", "重复下单", "狂点"],
        status="tentative",
    )
    db.commit()

    service = RetrievalService(db)
    first = service.memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="充值有问题",
        session_id="clarify-s",
    )
    second = service.memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="不是金额，是狂点支付会重复扣",
        session_id="clarify-s",
    )
    assert first["recall_mode"] != "empty"
    assert second["recall_mode"] != "empty"
    assert second["results"][0]["memory_id"] == repeat.id
    logs = list(db.scalars(select(AgentQueryLog).order_by(AgentQueryLog.id)).all())
    assert len(logs) == 2
    assert logs[1].recall_mode == second["recall_mode"]
    assert logs[1].returned_count == second["returned_count"]
    assert logs[1].input_summary["prev_query_id"] == logs[0].id
    assert logs[1].output_summary["results"]
    items, total = service.list_query_logs(project.id)
    assert total == 2
    assert items[0]["id"] == logs[1].id
    assert items[0]["task"].startswith("不是金额")
    assert service.clear_query_logs(project.id) == 2
    assert service.list_query_logs(project.id)[1] == 0


def test_distinctive_rerank_beats_shared_token_neighbor(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "成功提示倒计时拦截重复请求",
        "短时间重复请求要拦，倒计时结束再发",
        ["倒计时", "重复请求", "拦截"],
        status="tentative",
    )
    wanted = _memory(
        db,
        project.id,
        current.id,
        "购买弹窗连点会重复下单",
        "用户狂点支付会重复扣款",
        ["连点", "重复下单", "狂点"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="狂点支付会重复扣",
    )
    assert context["results"][0]["memory_id"] == wanted.id


def test_casual_repeat_pay_is_not_empty(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "充值优惠券实付金额",
        "选券后实付对不上，0 元怎么走",
        ["金额", "优惠券", "0元"],
        status="tentative",
    )
    repeat = _memory(
        db,
        project.id,
        current.id,
        "购买弹窗连点会重复下单",
        "用户狂点支付会重复扣款",
        ["连点", "重复下单", "狂点"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="充值页狂点支付按钮会不会重复扣",
    )
    assert context["recall_mode"] != "empty"
    assert context["results"][0]["memory_id"] == repeat.id


def test_casual_coupon_amount_is_not_empty(db) -> None:
    project, current, _ = _project_with_repos(db)
    amount = _memory(
        db,
        project.id,
        current.id,
        "充值优惠券实付金额",
        "选券后实付对不上，0 元怎么走",
        ["金额", "优惠券", "0元", "实付"],
        status="tentative",
    )
    _memory(
        db,
        project.id,
        current.id,
        "购买弹窗连点会重复下单",
        "用户狂点支付会重复扣款",
        ["连点", "重复下单", "狂点"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="选了券实付对不上，有的还是0块钱",
    )
    assert context["recall_mode"] != "empty"
    assert context["results"][0]["memory_id"] == amount.id


def test_clarifying_turn_does_not_prefer_countdown_repeat(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "充值优惠券实付金额",
        "选券后实付对不上，0 元怎么走",
        ["金额", "优惠券", "0元"],
        status="tentative",
    )
    repeat = _memory(
        db,
        project.id,
        current.id,
        "购买弹窗连点会重复下单",
        "用户狂点支付会重复扣款",
        ["连点", "重复下单", "狂点"],
        status="tentative",
    )
    _memory(
        db,
        project.id,
        current.id,
        "成功提示倒计时拦截重复请求",
        "短时间重复请求要拦，倒计时结束再发",
        ["倒计时", "重复请求", "拦截"],
        status="tentative",
    )
    db.commit()

    service = RetrievalService(db)
    service.memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="充值有问题",
        session_id="clarify-repeat",
    )
    second = service.memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="不是金额，是狂点支付会重复扣",
        session_id="clarify-repeat",
    )
    assert second["results"][0]["memory_id"] == repeat.id


def test_only_scss_without_style_memory_is_empty(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "CKEditor 样式覆盖",
        "富文本 scss 覆盖导致编辑器乱",
        ["CKEditor", "scss"],
        status="tentative",
    )
    _memory(
        db,
        project.id,
        current.id,
        "协议层领取桥",
        "packages/common 协议和领取逻辑",
        ["协议", "领取", "common"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="这个PR只改scss，packages/common和协议层都别动",
    )
    assert context["recall_mode"] == "empty"


def test_vague_gift_hint_uses_actual_candidates(db) -> None:
    project, current, _ = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "礼包状态别只信 status",
        "礼包 state.status 为 3 仍显示可领",
        ["礼包", "status"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="礼包有问题",
    )
    assert context["hint"]
    assert context["hint"]
    assert "礼包" in (context["hint"] or "")


def test_negation_does_not_rank_forbidden_layer_first(db) -> None:
    project, current, _ = _project_with_repos(db)
    style = _memory(
        db,
        project.id,
        current.id,
        "礼包横幅样式兼容",
        "端内礼包横幅图花了字体糊",
        ["横幅", "webp", "样式"],
        status="tentative",
    )
    _memory(
        db,
        project.id,
        current.id,
        "协议层领取桥",
        "packages/common 协议和领取逻辑",
        ["协议", "领取", "common"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="只改礼包横幅样式，协议和领取逻辑先别动",
    )
    assert context["results"]
    assert context["results"][0]["memory_id"] == style.id


def test_ios_h5_container_keeps_hinted_repo(db) -> None:
    project, current, ios_repo = _project_with_repos(db, name="2048")
    wanted = _memory(
        db,
        project.id,
        current.id,
        "iOS 选券回来卡片没了",
        "端内 H5 充值选券支付完回跳丢状态",
        ["选券", "充值", "回跳"],
        status="tentative",
    )
    _memory(
        db,
        project.id,
        ios_repo.id,
        "iOS 安全区适配",
        "原生安全区和包体上传",
        ["安全区", "包体"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="线上反馈：iOS进充值选好券支付完回来卡片没了",
    )
    assert context["repository"]["name"] == "shop-web"
    assert context["scope"]["primary_switched"] is False
    ids = [item["memory_id"] for item in context["results"]]
    assert wanted.id in ids[:2]


def test_confidence_does_not_outrank_keyword(db) -> None:
    project, current, _ = _project_with_repos(db)
    neighbor = _memory(
        db,
        project.id,
        current.id,
        "活动浮层关闭",
        "关闭弹窗时清拦截",
        ["浮层", "弹窗"],
        status="tentative",
    )
    neighbor.confidence = 0.95
    wanted = _memory(
        db,
        project.id,
        current.id,
        "购买完成功浮层抽公共",
        "成功提示每个活动都复制一坨，抽个公共浮层",
        ["成功提示", "浮层", "抽公共"],
        status="tentative",
    )
    wanted.confidence = 0.4
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="买完成功提示每个活动都复制一坨，抽个公共的吧",
    )
    assert context["results"][0]["memory_id"] == wanted.id


def test_identifier_terms_split_camel_digits_and_acronyms() -> None:
    terms = RetrievalService._identifier_terms("2048iOSGame")
    assert {"2048", "game"} <= terms
    assert "ios" in terms
    xml = RetrievalService._identifier_terms("getHTTPResponse")
    assert {"get", "http", "response", "httpresponse"} <= xml


def test_other_repo_memory_can_outrank_hinted_repo_on_keywords(db) -> None:
    project, current, other = _project_with_repos(db)
    _memory(
        db,
        project.id,
        current.id,
        "活动横幅样式",
        "横幅图花了",
        ["横幅", "样式"],
        status="tentative",
    )
    wanted = _memory(
        db,
        project.id,
        other.id,
        "原生支付验单",
        "充值结果要以服务端验单为准，不要信本地金额",
        ["充值", "验单", "支付"],
        status="tentative",
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref=project.id,
        repository_ref=current.name,
        task="充值结果验单，不要信本地金额",
    )
    assert context["repository"]["name"] == current.name
    assert context["results"][0]["memory_id"] == wanted.id
    assert context["results"][0]["repository_name"] == other.name
    assert context["searched_repository_count"] == 2
    assert context["result_repository_count"] >= 1
    names = {item["name"] for item in context["results_by_repository"]}
    assert other.name in names


def test_passed_repo_stays_primary_even_if_query_matches_another_repo_name(db) -> None:
    project, current, ios_repo = _project_with_repos(db, name="2048")
    _memory(
        db,
        project.id,
        ios_repo.id,
        "WKWebView 键盘顶起",
        "iOS WKWebView 输入框键盘顶起页面",
        ["WKWebView", "键盘", "input"],
    )
    local = _memory(
        db,
        project.id,
        current.id,
        "AccountOffboard 跳转拼接",
        "多页 H5 在子路径部署时拼接跳转 URL",
        ["AccountOffboard", "Verify"],
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref="",
        repository_ref=current.name,
        task="iOS WKWebView input 输入框键盘顶起",
        files=["apps/packages/account/src/components/AccountOffboard/Verify/index.tsx"],
    )
    assert context["repository"]["name"] == "shop-web"
    assert context["scope"]["primary_switched"] is False
    ids = [item["memory_id"] for item in context["results"]]
    assert local.id in ids or context["results"]


def test_task_infers_project_and_repo_without_hint(db) -> None:
    project, _, ios_repo = _project_with_repos(db, name="2048")
    other = Project(name="游戏工具")
    db.add(other)
    db.flush()
    db.add(
        Repository(
            project_id=other.id,
            name="wiki",
            gitlab_project_id="33",
            clone_url="https://gitlab.example.com/tool/wiki.git",
        )
    )
    db.commit()

    context = RetrievalService(db).memory_context(
        project_ref="",
        repository_ref="",
        task="2048 iOS WKWebView 键盘",
    )
    assert context["project"]["id"] == project.id
    assert context["repository"]["id"] == ios_repo.id


def test_ambiguous_task_without_repo_lists_projects(db) -> None:
    _project_with_repos(db, name="mall")
    other = Project(name="游戏工具")
    db.add(other)
    db.flush()
    db.add(
        Repository(
            project_id=other.id,
            name="wiki",
            gitlab_project_id="33",
            clone_url="https://gitlab.example.com/tool/wiki.git",
        )
    )
    db.commit()

    with pytest.raises(ValueError, match="无法从问题判断 Project"):
        RetrievalService(db).memory_context(
            project_ref="",
            repository_ref="",
            task="随便查一下",
        )


def test_compact_evidence_payload_truncates_cluster_and_filters_path() -> None:
    payload = {
        "diff": "diff --git a/appClip/src/components/input/index.tsx b/appClip/src/components/input/index.tsx\n"
        + ("x" * 4000)
        + "\ndiff --git a/appClip/src/other.tsx b/appClip/src/other.tsx\n"
        + ("y" * 200),
        "changed_files": [{"path": f"file-{index}.tsx"} for index in range(30)]
        + [{"path": "appClip/src/components/input/index.tsx"}],
        "shas": [str(index) for index in range(20)],
    }
    compact = compact_evidence_payload(payload, file_path="components/input")
    assert compact["diff_truncated"] is True
    assert "input/index.tsx" in compact["diff"]
    assert "other.tsx" not in compact["diff"]
    assert compact["changed_files_count"] == 31
    assert all("input" in str(item) for item in compact["changed_files"])
    assert compact["sha_count"] == 20
