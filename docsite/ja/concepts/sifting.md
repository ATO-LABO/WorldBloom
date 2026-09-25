# Sifting

Sifting は、QD 格子に残った候補を人が読んで選ぶ工程です。画面上では格子のセルをクリックして候補の「物語」「選択とメモ」「実験データ」を確認し、気に入ったものを採用（保留に戻すことも可能）します。採用した候補だけが、次のあらすじ・本文生成の対象になります。出来事の生成そのものに LLM は関与せず、Sifting で選ばれた道のりだけを LLM が文章にします。

Sifting の設計意図は ATOM-BOX の [WorldBloom の仕組み](https://www.atom-box.jp/worldbloom/how-it-works/) を参照してください。
