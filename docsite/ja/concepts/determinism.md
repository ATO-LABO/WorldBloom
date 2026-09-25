# 決定論

個体の評価は純関数として設計されています: `(genome, seed, world, precedent, engine hash)` が同じなら、出力ログ `layers.jsonl` はバイト単位で一致します。乱数はシミュレーション用と GA 用に分離され、Policy（遺伝子）自体は乱数を消費しません。候補生成は決定論的で、同点のタイブレークは名前順です。エンジンのコードを変えると乱数列が変わり同じ genome でも別の物語になるため、格子には genome だけでなく模範ランのログとエンジンのハッシュを保存し、物語は「再現手順」ではなく「系譜」として扱います。

回帰テスト（決定論・中立遺伝子の無変調・前提違反ゼロなど）は `python -m unittest discover -s tests` で実行できます。詳しい設計根拠は [docs/2026-09-11_gapengine-detailed-design.md](https://github.com/ATO-LABO/WorldBloom/blob/main/docs/2026-09-11_gapengine-detailed-design.md) を参照してください。
