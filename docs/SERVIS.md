# Windows Servisi (winsw) — kopruyu arka planda calistir

Konsol penceresi acik tutmak istemiyorsan bridge'i Windows servisi yap.
`winsw` tek dosyalik acik kaynak sarmalayicidir.

## Kurulum

1. `WinSW-x64.exe` indir, `telegram-bridge.exe` diye kopyala (paket kokune degil,
   orn. `C:\tools\telegram-bridge\`).
2. Yanina `telegram-bridge.xml` koy:

```xml
<service>
  <id>telegram-bridge</id>
  <name>OpenCode Telegram Bridge</name>
  <description>Telegram &lt;-&gt; opencode koprusu</description>
  <executable>powershell.exe</executable>
  <!-- Asagidaki iki yolu KENDI kurulum yolunla degistir (bosluk/tilde kullanma) -->
  <arguments>-NoProfile -ExecutionPolicy Bypass -File "C:\tools\opencode-telegram\scripts\start-bridge.ps1"</arguments>
  <workingdirectory>C:\tools\opencode-telegram</workingdirectory>
  <log mode="roll" />
  <onfailure action="restart" delay="10 sec" />
</service>
```

3. Yonetici terminalde:

```
telegram-bridge.exe install
telegram-bridge.exe start
telegram-bridge.exe status
```

Kaldirma: `telegram-bridge.exe stop` + `telegram-bridge.exe uninstall`.

## Notlar

- `.env` paket kokunde olmali (bridge calisma dizininden okur).
- Servis olarak calisirken konsol log'u winsw log dosyasina gider;
  hata ayiklamak icin once konsoldan calistir (`start-bridge.ps1`).
- Basit alternatif: `schtasks` ile logon'da baslatma (bkz. KURULUM.md).
- Bridge beklenmedik sekilde olurse veya poll loop'u ardisik hatalarla kilitlenirse
  varsayilan chat'e "Kopru durdu" pingu atmaya calisir (`_notify_death`); Ctrl+C ile
  kapatmada ping atilmaz. `onfailure action="restart"` bu yuzden onemlidir.
