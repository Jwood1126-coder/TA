"""One-time script: fix activity titles/descriptions that reference eliminated hotels.

Confirmed accommodation chain:
  - Sotetsu Fresa Inn (Tokyo, Apr 6-9)
  - TAKANOYU (Takayama, Apr 9-12)
  - Tsukiya-Mikazuki (Kyoto, Apr 12-14)
  - Kyotofish Miyagawa (Kyoto, Apr 14-16)
  - Hotel The Leben Osaka (Osaka, Apr 16-18)

NO Kanazawa overnight — Day 7-8 is transit only.
"""
import sqlite3
import sys
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'japan_trip.db')


def fix(conn):
    cur = conn.cursor()
    changes = []

    # 1. id=35: generic "your ryokan" → confirmed hotel name
    cur.execute("SELECT title FROM activity WHERE id=35")
    row = cur.fetchone()
    if row and "your ryokan" in row[0]:
        cur.execute("UPDATE activity SET title='Check into TAKANOYU' WHERE id=35")
        changes.append("id=35: 'Check into your ryokan' → 'Check into TAKANOYU'")

    # 2. id=42: generic ryokan onsen reference → specific
    cur.execute("SELECT title FROM activity WHERE id=42")
    row = cur.fetchone()
    if row and "depending on the ryokan" in row[0]:
        cur.execute(
            "UPDATE activity SET title='After dinner: soak in the onsen bath at TAKANOYU' WHERE id=42"
        )
        changes.append("id=42: generic ryokan onsen → 'at TAKANOYU'")

    # 3. id=48: "Check into K's House Takayama" on Day 6 — stale duplicate
    #    Real check-in is Day 5 (id=35). Eliminate this.
    cur.execute("SELECT title, is_eliminated FROM activity WHERE id=48")
    row = cur.fetchone()
    if row and "K's House" in row[0] and not row[1]:
        cur.execute("UPDATE activity SET is_eliminated=1 WHERE id=48")
        changes.append("id=48: eliminated stale 'Check into K's House' (duplicate of Day 5 check-in)")

    # 4. id=54: "Check out of K's House Takayama" → confirmed hotel
    cur.execute("SELECT title FROM activity WHERE id=54")
    row = cur.fetchone()
    if row and "K's House" in row[0]:
        cur.execute("UPDATE activity SET title='Check out of TAKANOYU' WHERE id=54")
        changes.append("id=54: 'Check out of K''s House' → 'Check out of TAKANOYU'")

    # 5. id=60: "Check into Kaname Inn Tatemachi" — NO Kanazawa overnight. Eliminate.
    cur.execute("SELECT title, is_eliminated FROM activity WHERE id=60")
    row = cur.fetchone()
    if row and "Kaname Inn" in row[0] and not row[1]:
        cur.execute("UPDATE activity SET is_eliminated=1 WHERE id=60")
        changes.append("id=60: eliminated 'Check into Kaname Inn' (no Kanazawa overnight)")

    # 6. id=100: "Check into Airbnb machiya" → confirmed hotel
    cur.execute("SELECT title FROM activity WHERE id=100")
    row = cur.fetchone()
    if row and "Airbnb machiya" in row[0]:
        cur.execute(
            "UPDATE activity SET title='Check into Kyotofish Miyagawa', description=NULL WHERE id=100"
        )
        changes.append("id=100: 'Check into Airbnb machiya' → 'Check into Kyotofish Miyagawa'")

    # 7. id=13: description "right near your hotel" → specific
    cur.execute("SELECT description FROM activity WHERE id=13")
    row = cur.fetchone()
    if row and row[0] and "your hotel" in row[0]:
        new_desc = row[0].replace("your hotel", "Sotetsu Fresa Inn")
        cur.execute("UPDATE activity SET description=? WHERE id=13", (new_desc,))
        changes.append("id=13: 'right near your hotel' → 'right near Sotetsu Fresa Inn'")

    conn.commit()
    return changes


if __name__ == '__main__':
    db = sys.argv[1] if len(sys.argv) > 1 else DB_PATH
    print(f"Fixing stale narratives in: {db}")
    conn = sqlite3.connect(db)
    changes = fix(conn)
    conn.close()
    for c in changes:
        print(f"  ✓ {c}")
    print(f"\n{len(changes)} fixes applied.")
