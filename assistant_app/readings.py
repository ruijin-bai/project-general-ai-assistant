"""Exact source-linked utility reading differences; no authoritative consumption claim."""

import json
import re
from datetime import datetime, timezone
from decimal import Context, Decimal, localcontext

from .store import AppError, dump, now, string, uid
from .structured import SAFE_ID, StructuredService, _strict

SCOPE = "本人私有水电读数核对与相邻输入差；不代表传感器核真、权威实绩、真实耗量、计费或支付"
READING = re.compile(r"[0-9]{1,18}(?:\.[0-9]{1,6})?")


def _instant(value):
    if not isinstance(value, str) or len(value) > 100 or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T", value):
        raise AppError("invalid_observed_at", "读数时点须为有明确UTC偏移的ISO日期时间。")
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise AppError("invalid_observed_at", "读数时点无效或缺明确时区，不补默认时区。") from None


class UtilitiesService(StructuredService):
    def _readings(self, facts):
        return facts.get("__readings", {"schema_version": 1, "input_revision": None, "structure_version": 0, "items": []})

    def _analysis(self, items, stale):
        groups, intervals, issues = {}, [], []
        for item in items:
            groups.setdefault((item["meter_ref"], item["unit"]), []).append(item)
            if item["confirmation"] != "local_checked":
                issues.append({"kind": "confirmation_pending", "row_ids": [item["id"]], "reason": "该读数为候选，尚未本人核对原来源，不计算相邻差。"})
        with localcontext(Context(prec=40)):
            for (meter, unit), readings in groups.items():
                ordered = sorted(readings, key=lambda item: (_instant(item["observed_at"]), item["id"]))
                instants, conflicts = {}, set()
                for item in ordered:
                    instants.setdefault(_instant(item["observed_at"]), []).append(item)
                for same_time in instants.values():
                    if len({Decimal(item["reading"]) for item in same_time}) > 1:
                        row_ids = [item["id"] for item in same_time]
                        conflicts.update(row_ids)
                        issues.append({"kind": "conflicting_same_instant", "row_ids": row_ids, "reason": "同仪表、同单位、同UTC时点读数不同；真实读数待核，不计算涉及这些点的差。"})
                for first, last in zip(ordered, ordered[1:]):
                    if first["id"] in conflicts or last["id"] in conflicts or first["confirmation"] != "local_checked" or last["confirmation"] != "local_checked":
                        continue
                    if _instant(last["observed_at"]) <= _instant(first["observed_at"]):
                        continue
                    difference = Decimal(last["reading"]) - Decimal(first["reading"])
                    if difference < 0:
                        issues.append({"kind": "reset_or_error_unknown", "row_ids": [first["id"], last["id"]], "reason": "相邻读数倒退，回零、换表或抄录错误原因未知；不报告负耗量。"})
                        continue
                    intervals.append({"meter_ref": meter, "unit": unit, "from_id": first["id"], "to_id": last["id"], "from_observed_at": first["observed_at"], "to_observed_at": last["observed_at"], "difference": format(difference, "f"), "stale": stale})
        if stale:
            issues.append({"kind": "sources_stale", "row_ids": [item["id"] for item in items], "reason": "事项来源或事实修订已变化；原输入和原差值保留，须明确重核后使用。"})
        return intervals, issues

    def _view(self, connection, matter, facts):
        meta = self._readings(facts)
        stale = meta["input_revision"] is not None and meta["input_revision"] != matter["revision_no"]
        items = [{**item, "refs": self._refs(connection, matter["id"], item["refs"])} for item in meta["items"]]
        intervals, issues = self._analysis(items, stale)
        return {"id": matter["id"], "version": matter["version"], "revision_no": matter["revision_no"], "input_revision": meta["input_revision"], "structure_version": meta["structure_version"],
                "stale": stale, "items": items, "intervals": intervals, "issues": issues, "scope": SCOPE}

    def get_readings(self, matter_id):
        with self.store.connect() as connection:
            _, matter, facts = self._owner(connection, matter_id, "utilities")
            return self._view(connection, matter, facts)

    def _limit(self, data):
        try:
            if len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 131072:
                raise ValueError()
        except (TypeError, ValueError, UnicodeError):
            raise AppError("reading_limit", "读数请求须为有效JSON且最多128KiB。") from None

    def _baseline(self, matter, meta, data):
        self.store._check_version(matter, data)
        structure_key = "base_structure_version" if "base_structure_version" in data else "structure_version"
        if type(data["input_revision"]) is not int or data["input_revision"] != matter["revision_no"] or type(data[structure_key]) is not int or data[structure_key] != meta["structure_version"]:
            raise AppError("reading_conflict", "事实或读数结构版本已变化，请读回后保留人工修改。", 409)

    def _item(self, connection, matter_id, item):
        _strict(item, {"id", "meter_ref", "unit", "reading", "observed_at", "refs", "confirmation", "note"})
        if not isinstance(item["id"], str) or not SAFE_ID.fullmatch(item["id"]):
            raise AppError("invalid_id", "读数项须有稳定安全ID，最多80字符。")
        if not isinstance(item["reading"], str) or not READING.fullmatch(item["reading"]):
            raise AppError("invalid_reading", "读数须为非负显式十进制字符串，最多18位整数、6位小数；不接受指数、布尔或非有限值。")
        _instant(item["observed_at"])
        if item["confirmation"] not in ("candidate", "local_checked"):
            raise AppError("invalid_confirmation", "读数仅可为候选或本人核对原来源，不代表传感器核真。")
        if not isinstance(item["note"], str) or len(item["note"]) > 2000:
            raise AppError("invalid_note", "备注须为最多2000字文字，可空。")
        meter, unit = string(item["meter_ref"], "明确仪表引用", 200), string(item["unit"], "明确读数单位", 80)
        if meter in {"未知", "待核", "unknown"} or unit in {"未知", "待核", "unknown"}:
            raise AppError("reading_pending", "仪表或单位不明，请先保留原文待核，不进入可计算明细。")
        refs = self._refs(connection, matter_id, item["refs"], require_usable=True)
        return {"id": item["id"], "meter_ref": meter, "unit": unit, "reading": item["reading"], "observed_at": item["observed_at"], "confirmation": item["confirmation"], "note": item["note"].strip(),
                "refs": [{key: ref[key] for key in ("source_id", "start_line", "end_line")} for ref in refs]}

    def save_readings(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "base_structure_version", "upserts", "removed_ids"})
        self._limit(data)
        if not isinstance(data["upserts"], list) or not isinstance(data["removed_ids"], list) or len(data["upserts"]) > 40 or len(data["removed_ids"]) > 40 or not (data["upserts"] or data["removed_ids"]):
            raise AppError("reading_limit", "每次最多40项，删除须明确列ID，空列表不会清空。")
        with self.store.transaction() as connection:
            actor, matter, facts = self._owner(connection, matter_id, "utilities")
            key, fingerprint, previous = self._request(connection, actor, "utilities_save", matter_id, data, key)
            if previous is not None:
                return self._view(connection, matter, facts)
            meta = self._readings(facts)
            self._baseline(matter, meta, data)
            items = {item["id"]: item for item in meta["items"]}
            removed, seen = data["removed_ids"], set()
            if any(not isinstance(item_id, str) or item_id not in items for item_id in removed) or len(set(removed)) != len(removed):
                raise AppError("invalid_removed_ids", "仅可明确删除当前本事项读数ID，不能重复。")
            for incoming in data["upserts"]:
                item = self._item(connection, matter_id, incoming)
                if item["id"] in seen or item["id"] in removed:
                    raise AppError("invalid_id", "读数ID重复或同时删除，请保留明确修订。")
                seen.add(item["id"])
                items[item["id"]] = item
            for item_id in removed:
                del items[item_id]
            if len(items) > 40:
                raise AppError("reading_limit", "本事项最多40个读数项。")
            measurements = set()
            for item in items.values():
                measurement = (item["meter_ref"], item["unit"], _instant(item["observed_at"]), Decimal(item["reading"]))
                if measurement in measurements:
                    raise AppError("duplicate_reading", "同仪表、单位、实际时点和数值的重复读数，请保留一条；不自动去重。")
                measurements.add(measurement)
            revision = matter["revision_no"] + 1
            facts["__readings"] = {"schema_version": 1, "input_revision": revision, "structure_version": meta["structure_version"] + 1, "items": list(items.values())}
            connection.execute("INSERT INTO revisions VALUES(?,?,?,?,?)", (matter_id, revision, dump(facts), now(), actor["id"]))
            self.store._cancel(connection, matter_id, "读数修订，停止旧准备；人工原稿与其他明细保留")
            status = matter["assistant_status"] if matter["assistant_status"] in {"paused", "handoff"} else "waiting"
            self.store._bump(connection, matter_id, revision_no=revision, preparation_status="needs_review", assistant_status=status)
            self.store._event(connection, matter_id, "utilities_readings_saved", "本人保存有真实来源位置的读数修订；相邻输入差不当真实耗量或计费。")
            return self._remember(connection, key, fingerprint, "utilities_save", matter_id, self._view(connection, self.store._matter(connection, matter_id), facts))

    def render_readings(self, matter_id, data, key):
        _strict(data, {"expected_version", "input_revision", "structure_version"})
        self._limit(data)
        with self.store.transaction() as connection:
            actor, matter, facts = self._owner(connection, matter_id, "utilities")
            key, fingerprint, previous = self._request(connection, actor, "utilities_render", matter_id, data, key)
            if previous is not None:
                return previous
            self._baseline(matter, self._readings(facts), data)
            if matter["assistant_status"] in {"paused", "handoff"}:
                raise AppError("assistant_stopped", "未来准备已停止；仍可保存人工读数修正。", 409)
            view = self._view(connection, matter, facts)
            self._ensure_preparation_sources(connection, matter_id, [ref['source_id'] for item in view['items'] for ref in item['refs']])
            if view["stale"]:
                raise AppError("readings_stale", "来源或事实已修订，请明确重核后保存，旧成果保留。", 409)
            if not view["items"]:
                raise AppError("readings_empty", "未提供有真实来源位置的读数，不能生成空核对稿。")
            lines = ["# 水电读数核对准备稿", "", SCOPE, "事实修订：" + str(matter["revision_no"]), "表倍率、回零、损耗、费用、权威实绩及正式计费均待核。", "", "## 原输入"]
            for item in view["items"]:
                lines += ["- " + " / ".join(item[key] for key in ("meter_ref", "unit", "reading", "observed_at", "confirmation")), item["note"]]
                for ref in item["refs"]:
                    lines.append("来源 " + ref["source_id"] + " 第" + str(ref["start_line"]) + "–" + str(ref["end_line"]) + "行（真实摘录见私有读数核对条目）。")
            lines += ["", "## 程序精确相邻输入差"]
            for interval in view["intervals"]:
                lines.append("- " + interval["meter_ref"] + " / " + interval["unit"] + " / " + interval["from_observed_at"] + " → " + interval["to_observed_at"] + "：" + interval["difference"])
            if not view["intervals"]:
                lines.append("尚无可计算相邻差；不将未知当零。")
            lines += ["", "## 疑点与待核"] + ["- " + issue["reason"] for issue in view["issues"]]
            content, artifact_id = "\n".join(lines), uid()
            connection.execute("INSERT INTO artifacts(id,version,matter_id,type,title,content,status,input_revision,created_at) VALUES(?,1,?,?,?,?,?,?,?)", (artifact_id, matter_id, "utilities_preparation", "水电读数核对准备稿", content, "structured_draft", matter["revision_no"], now()))
            self.store._bump(connection, matter_id)
            self.store._event(connection, matter_id, "utilities_rendered", "已保存新的读数核对准备稿；未记权威实绩、真实耗量、计费或支付，原人工版本保留。")
            return self._remember(connection, key, fingerprint, "utilities_render", matter_id, {"artifact_id": artifact_id, "version": 1, "type": "utilities_preparation", "input_revision": matter["revision_no"], "content": content, "scope": SCOPE})
