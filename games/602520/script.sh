flatpak install com.github.Matoking.protontricks
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/update/update.exe
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/update/updspapi.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/update/spcustom.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/wshom.ocx
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/wscript.hlp
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/wscript.exe
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/spuninst.exe
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/cscript.exe
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/vbscript.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/spmsg.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/scrrun.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/scrobj.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/jscript.dll
chmod 666 /home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx/dosdevices/c:/windows/syswow64/dispex.dll
flatpak run com.github.Matoking.protontricks 602520 wmp11

cd /home/deck/Downloads
git clone https://github.com/z0z0z/mf-install.git
cd mf-install/

WINEPREFIX="/home/deck/.local/share/Steam/steamapps/compatdata/602520/pfx" PROTON="/home/deck/.local/share/Steam/steamapps/common/Proton 7.0" ./mf-install.sh -proton
