"""Yönetim › Kapı haritası (1.21.0; kullanıcı isteği 1 Kasım 2026: "adminin bu oluşan haritadaki yoğun doğrulanmış sokak adlarını, bina
numaralarını görebildiği bir harita veritabanı önizleme ekranı olmalı").

Ortak kapı katmanı (door_points) zaten kuryelerin teslimatlarıyla doluyor; bu uçlar yalnız YÖNETİMİN ona bakmasını sağlar:
- özet: il › ilçe › mahalle › sokak kırılımında kaç kapı, kaçı doğrulanmış / bekliyor / tartışmalı / gizli (+ harita için merkez nokta),
- noktalar: süzgece ya da harita penceresine (bbox) göre kapılar,
- ayrıntı: oy sayıları kaynağa göre (kimin oy verdiği YOK: bildirenler zaten geri çevrilemez kodla tutulur, burada o kod da gösterilmez),
- silme (yanlış nokta) ve CSV indirme; ikisi de yönetim işlem günlüğüne yazılır.
Katmanda kişi bilgisi yoktur (ad, telefon, daire / kat): yalnız il / ilçe / mahalle / sokak / kapı no / site adı + koordinat + puan.

Durumlar (uygulamadaki "doğrulanmış" kuralıyla aynı):
  verified  = gizli değil, puan ≥ DOOR_TRUST_SCORE ve puan > yanlış puanı
  pending   = gizli değil, doğrulanmamış, "yanlış" oyu yok (genelde tek bildirim)
  disputed  = gizli değil, doğrulanmamış, "yanlış" oyu var
  hidden    = yanlış oyları ağır bastığı için uygulamada gösterilmiyor
"""
import csv
import io
import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import and_, case, func, not_
from sqlalchemy.orm import Session

from . import places
from .admin import audit, current_admin
from .db import get_db
from .models import Admin, DoorPoint, DoorReport, now

STATUSES = ("verified", "pending", "disputed", "hidden")
_KEY = re.compile(r"^[a-z0-9 ]{0,120}$")  # anahtarlar katmandaki gibi: küçük harf, düz Latin


def _key(v: str, name: str) -> str:
    v = (v or "").strip()
    if name == "il" and v == "-":  # 1.22.0: ili belirsiz noktalar (ilçe resmi listede bulunamadı)
        return v
    if not _KEY.match(v):
        raise HTTPException(400, f"Geçersiz {name}")
    return v


def make_router(trust_score: float = 2.0) -> APIRouter:
    r = APIRouter()
    P = DoorPoint
    is_verified = and_(P.hidden.is_(False), P.score >= trust_score, P.score > P.wrong)
    cond = {
        "verified": is_verified,
        "pending": and_(P.hidden.is_(False), not_(is_verified), P.wrong <= 0),
        "disputed": and_(P.hidden.is_(False), not_(is_verified), P.wrong > 0),
        "hidden": P.hidden.is_(True),
    }

    def status_of(p: DoorPoint) -> str:
        if p.hidden:
            return "hidden"
        if (p.score or 0) >= trust_score and (p.score or 0) > (p.wrong or 0):
            return "verified"
        return "disputed" if (p.wrong or 0) > 0 else "pending"

    def count_cols():
        """(toplam, doğrulanmış, bekleyen, tartışmalı, gizli) sütunları"""
        n = lambda c: func.coalesce(func.sum(case((c, 1), else_=0)), 0)  # noqa: E731
        return [func.count(P.id), n(cond["verified"]), n(cond["pending"]), n(cond["disputed"]), n(cond["hidden"])]

    def filtered(db: Session, p: str = "", d: str = "", n: str = "", s: str = "", k: str = "", status: str = ""):
        q = db.query(P)
        if p:
            q = q.filter(P.province_k == ("" if p == "-" else p))
        if d:
            q = q.filter(P.district_k == d)
        if n:
            q = q.filter(P.neigh_k == n)
        if s:
            q = q.filter(P.street_k == s, P.street_kind == k)
        if status:
            if status not in STATUSES:
                raise HTTPException(400, "Geçersiz durum")
            q = q.filter(cond[status])
        return q

    def place(x: DoorPoint) -> str:
        parts = [x.neighborhood, x.district, x.province]
        return ", ".join(v for v in parts if v)

    def point_out(x: DoorPoint) -> dict:
        return {
            "id": x.id, "door": x.door_k, "n": x.door_n, "street": x.street, "kind": x.street_kind, "site": x.site or "",
            "neighborhood": x.neighborhood, "district": x.district, "province": x.province,
            "lat": round(x.lat, 6), "lon": round(x.lon, 6),
            "score": round(x.score or 0, 2), "wrong": round(x.wrong or 0, 2), "confirms": x.confirms or 0,
            "status": status_of(x), "created": x.created_at.isoformat() if x.created_at else None,
            "updated": x.updated_at.isoformat() if x.updated_at else None,
        }

    @r.get("/v1/admin/doors/summary")
    def summary(p: str = "", d: str = "", n: str = "", a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Kırılım (1.22.0): Türkiye › il › ilçe › mahalle › sokak. İl yoksa iller, il varsa ilçeler, ilçe varsa mahalleler, mahalle
        varsa sokaklar. Her satırda sayılar + harita için merkez + işaretler (resmi listede mi, konum ilden çok uzak mı)."""
        p, d, n = _key(p, "il"), _key(d, "ilçe"), _key(n, "mahalle")
        tot = db.query(*count_cols()).select_from(P).one()
        week = db.query(func.count(P.id)).filter(P.created_at >= now() - timedelta(days=7)).scalar() or 0
        totals = {"total": tot[0], "verified": int(tot[1]), "pending": int(tot[2]), "disputed": int(tot[3]), "hidden": int(tot[4]), "new7d": week}
        if not p and not d:
            level, cols = "province", (P.province_k,)
            names = (func.max(P.province),)
        elif not d:
            level, cols = "district", (P.province_k, P.district_k)
            names = (func.max(P.province), func.max(P.district))
        elif not n:
            level, cols = "neighborhood", (P.province_k, P.district_k, P.neigh_k)
            names = (func.max(P.province), func.max(P.district), func.max(P.neighborhood))
        else:
            level, cols = "street", (P.province_k, P.district_k, P.neigh_k, P.street_k, P.street_kind)
            names = (func.max(P.province), func.max(P.district), func.max(P.neighborhood), func.max(P.street))
        rows = (
            filtered(db, p, d, n)
            .with_entities(*cols, *names, *count_cols(), func.avg(P.lat), func.avg(P.lon), func.max(P.updated_at), func.count(func.distinct(P.door_k)))
            .group_by(*cols)
            .all()
        )
        items = []
        nc, nn = len(cols), len(names)
        for row in rows:
            keys, nm, c, rest = row[:nc], row[nc:nc + nn], row[nc + nn:nc + nn + 5], row[nc + nn + 5:]
            it = {
                "p": keys[0] or "-", "d": keys[1] if nc > 1 else "", "n": keys[2] if nc > 2 else "", "s": keys[3] if nc > 3 else "", "k": keys[4] if nc > 4 else "",
                "province": nm[0] or "", "district": nm[1] if nn > 1 else "", "neighborhood": nm[2] if nn > 2 else "", "street": nm[3] if nn > 3 else "",
                "total": c[0], "verified": int(c[1]), "pending": int(c[2]), "disputed": int(c[3]), "hidden": int(c[4]),
                "lat": round(rest[0], 6) if rest[0] is not None else None, "lon": round(rest[1], 6) if rest[1] is not None else None,
                "updated": rest[2].isoformat() if rest[2] else None, "doors": rest[3],
            }
            it["name"] = {"province": it["province"] or "(il belirsiz)", "district": it["district"], "neighborhood": it["neighborhood"], "street": it["street"]}[level] or "(adsız)"
            # İşaretler: il / ilçe / mahalle resmi listede mi; noktaların ortası ilin merkezinden 250 km'den uzak mı
            it.update(places.flags(level, keys[0], it["d"], it["n"], it["lat"], it["lon"]))
            items.append(it)
        items.sort(key=lambda x: (-x["verified"], -x["total"], x["name"]))
        return {"level": level, "totals": totals, "items": items[:500], "more": max(0, len(items) - 500)}

    @r.get("/v1/admin/doors")
    def points(
        p: str = "", d: str = "", n: str = "", s: str = "", k: str = "", status: str = "", bbox: str = "",
        limit: int = Query(default=2000, ge=1, le=5000), a: Admin = Depends(current_admin), db: Session = Depends(get_db),
    ):
        """Kapı noktaları: süzgece göre ya da harita penceresinde (bbox = batı,güney,doğu,kuzey). En yeni güncellenen önce."""
        p, d, n, s, k = _key(p, "il"), _key(d, "ilçe"), _key(n, "mahalle"), _key(s, "sokak"), _key(k, "sokak türü")
        q = filtered(db, p, d, n, s, k, status)
        if bbox:
            try:
                w, so, e, no = (float(v) for v in bbox.split(","))
            except ValueError:
                raise HTTPException(400, "Geçersiz harita penceresi")
            if not (-180 <= w < e <= 180 and -90 <= so < no <= 90):
                raise HTTPException(400, "Geçersiz harita penceresi")
            q = q.filter(P.lon.between(w, e), P.lat.between(so, no))
        total = q.count()
        rows = q.order_by(P.updated_at.desc()).limit(limit).all()
        return {"total": total, "items": [point_out(x) for x in rows]}

    @r.get("/v1/admin/doors/export")
    def export(request: Request, p: str = "", d: str = "", n: str = "", status: str = "", a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """CSV (Excel'de Türkçe harfler doğru görünsün diye UTF-8 + BOM, ayraç noktalı virgül). Kişi bilgisi yok."""
        p, d, n = _key(p, "il"), _key(d, "ilçe"), _key(n, "mahalle")
        rows = filtered(db, p, d, n, status=status).order_by(P.province_k, P.district_k, P.neigh_k, P.street_k, P.door_n, P.door_k).limit(200000).all()
        names = {"verified": "doğrulanmış", "pending": "bekliyor", "disputed": "tartışmalı", "hidden": "gizli"}
        buf = io.StringIO()
        wr = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        wr.writerow(["il", "ilçe", "mahalle", "sokak", "tür", "kapı no", "site / bina", "enlem", "boylam", "durum", "onaylayan", "puan", "yanlış puanı", "ilk bildirim", "son güncelleme"])
        for x in rows:
            wr.writerow([
                x.province, x.district, x.neighborhood, x.street, x.street_kind, x.door_k, x.site or "", f"{x.lat:.6f}", f"{x.lon:.6f}",
                names[status_of(x)], x.confirms or 0, round(x.score or 0, 2), round(x.wrong or 0, 2),
                x.created_at.strftime("%Y-%m-%d %H:%M") if x.created_at else "", x.updated_at.strftime("%Y-%m-%d %H:%M") if x.updated_at else "",
            ])
        scope = " / ".join(v for v in (p, d, n) if v) or "tümü"
        audit(db, request, a, "door_export", "door", "", f"{len(rows)} nokta · {scope}" + (f" · {names.get(status, status)}" if status else ""))
        db.commit()
        stamp = now().strftime("%Y%m%d")
        return Response(
            content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="rota-kapi-haritasi-{stamp}.csv"', "Cache-Control": "no-store"},
        )

    @r.get("/v1/admin/doors/{pid}")
    def detail(pid: str, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Bir kapı noktası + oyların özeti (kaynağa ve oya göre sayı, ilk / son oy). Oy verenlerin kodu gösterilmez."""
        x = db.get(P, pid)
        if x is None:
            raise HTTPException(404, "Kapı noktası yok")
        votes = db.query(DoorReport.vote, DoorReport.source, func.count(DoorReport.id), func.min(DoorReport.created_at), func.max(DoorReport.created_at)).filter(
            DoorReport.point_id == pid).group_by(DoorReport.vote, DoorReport.source).all()
        out = point_out(x)
        out["place"] = place(x)
        out["votes"] = [{"vote": v, "source": src, "count": c, "first": f.isoformat() if f else None, "last": l.isoformat() if l else None} for v, src, c, f, l in votes]
        # Aynı kapı numarası için başka (uzak) aday var mı: çelişen bildirim
        out["rivals"] = db.query(func.count(P.id)).filter(P.id != x.id, P.district_k == x.district_k, P.neigh_k == x.neigh_k, P.street_k == x.street_k,
                                                          P.street_kind == x.street_kind, P.door_k == x.door_k).scalar() or 0
        return out

    @r.delete("/v1/admin/doors/{pid}")
    def delete(pid: str, request: Request, a: Admin = Depends(current_admin), db: Session = Depends(get_db)):
        """Yanlış kapı noktasını oylarıyla birlikte siler (uygulamada artık görünmez; yeni teslimler yeniden bildirebilir)."""
        x = db.get(P, pid)
        if x is None:
            return {"ok": True}
        # günlüğe sokak / kapı no YAZILMAZ (işlem günlüğünde adres olmaz kuralı); yalnız mahalle ve ilçe
        audit(db, request, a, "door_delete", "door", x.id, ", ".join(v for v in (x.neighborhood, x.district) if v) or "-")
        db.query(DoorReport).filter(DoorReport.point_id == pid).delete(synchronize_session=False)
        db.flush()  # oylar önce (PostgreSQL yabancı anahtar)
        db.delete(x)
        db.commit()
        return {"ok": True}

    return r
