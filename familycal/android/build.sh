#!/bin/bash
# Build + sign FamilyCalendar.apk without Gradle. Needs ~/android (jdk17, build-tools 34, platform 29, kotlinc).
set -e; cd "$(dirname "$0")"
export JAVA_HOME=~/android/jdk PATH=~/android/jdk/bin:$PATH
BT=~/android/build-tools/34.0.0; JAR=~/android/platforms/android-29/android.jar; KC=~/android/kotlinc
STD=$KC/lib/kotlin-stdlib.jar
rm -rf build; mkdir -p build/classes build/dex
# Default helper URL baked into the APK (the app's saved "url" setting overrides it). Set FAMCAL_URL or put
# FAMCAL_URL=http://<server>:8091 in android/local.cfg (gitignored); otherwise a placeholder is used.
[ -f local.cfg ] && . ./local.cfg
mkdir -p build/gen && printf 'package com.bakerk7.familycalendar\nobject BuildCfg { const val DEFAULT_URL = "%s" }\n' "${FAMCAL_URL:-http://familycal.local:8091}" > build/gen/BuildCfg.kt
$BT/aapt2 link -o build/res.apk -I $JAR --manifest AndroidManifest.xml -A assets --min-sdk-version 26 --target-sdk-version 29
$KC/bin/kotlinc -nowarn -jvm-target 1.8 -no-reflect -cp $JAR -d build/classes $(find src build/gen -name '*.kt') 2>&1 | grep -v '^warning' || true
test -f build/classes/com/bakerk7/familycalendar/MainActivity.class
# R8 shrinks kotlin-stdlib down to what we use (smaller dex = less RAM on the tablet).
cat > build/keep.pro <<'K'
-keep class com.bakerk7.familycalendar.MainActivity { *; }
-keep class com.bakerk7.familycalendar.RemReceiver { *; }
-dontobfuscate
-dontwarn **
K
~/android/jdk/bin/jar cf build/app.jar -C build/classes .
java -cp $BT/lib/d8.jar com.android.tools.r8.R8 --release --min-api 26 --lib $JAR --pg-conf build/keep.pro \
  --output build/dex build/app.jar $STD
cp build/res.apk build/unsigned.apk
python3 -c "import zipfile;zipfile.ZipFile('build/unsigned.apk','a').write('build/dex/classes.dex','classes.dex')"
$BT/zipalign -f 4 build/unsigned.apk build/aligned.apk
$BT/apksigner sign --ks /workspace/.ecal/kiosk.keystore --ks-pass file:/workspace/.ecal/kiosk.keystore.pass --out build/FamilyCalendar.apk build/aligned.apk
ls -la build/FamilyCalendar.apk
