"""Add optional school-scoped presentation assets without changing other maps."""
from pathlib import Path
import json,hashlib,datetime
r=Path('D:/simulator')
out=r/'artifacts/fixes/school-view-fidelity-20261119/runtime-source-01'
out.mkdir(parents=True,exist_ok=False)
p=r/'unreal/RMSim/Source/RMSim/CampusGym.cpp';b=p.read_bytes();s=b.decode('utf-8-sig')
anchor='    TMap<FString,UMaterialInterface*> Materials;'
insert='''    FString GlassMaterialPath=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_glass.M_glass");
    FString EnvironmentMaterialPath=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_environment.M_environment");
    FString EnvironmentCubemapPath=TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Textures/T_CampusCourtyard.T_CampusCourtyard");
    const FObj* Presentation=nullptr;
    if(Manifest->HasField(TEXT("presentation_assets")))
    {
        if(!Manifest->TryGetObjectField(TEXT("presentation_assets"),Presentation))
            return Fail(TEXT("presentation_assets must be an object"));
        auto OverridePath=[&](const TCHAR* Key,FString& Path)
        {
            if(!(*Presentation)->HasField(Key))return true;
            return (*Presentation)->TryGetStringField(Key,Path)&&
                Path.StartsWith(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/"),ESearchCase::CaseSensitive)&&
                !Path.Contains(TEXT(":"))&&FPackageName::IsValidObjectPath(Path,nullptr);
        };
        if(!OverridePath(TEXT("glass_material"),GlassMaterialPath)||
           !OverridePath(TEXT("environment_material"),EnvironmentMaterialPath)||
           !OverridePath(TEXT("environment_cubemap"),EnvironmentCubemapPath))
            return Fail(TEXT("invalid local presentation asset path"));
    }
    TMap<FString,UMaterialInterface*> Materials;'''
assert s.count(anchor)==1;s=s.replace(anchor,insert)
old='        const FString Path=FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_%s.M_%s"),Id,Id);'
new='''        FString Path=FString::Printf(TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Materials/M_%s.M_%s"),Id,Id);
        if(FString(Id)==TEXT("glass"))Path=GlassMaterialPath;
        else if(FString(Id)==TEXT("environment"))Path=EnvironmentMaterialPath;'''
assert s.count(old)==1;s=s.replace(old,new)
old='    UTextureCube* Courtyard=LoadObject<UTextureCube>(nullptr,TEXT("/Game/RMSim/Maps/rmuc2027/Arena/Campus/Textures/T_CampusCourtyard.T_CampusCourtyard"));'
new='    UTextureCube* Courtyard=LoadObject<UTextureCube>(nullptr,*EnvironmentCubemapPath);'
assert s.count(old)==1;s=s.replace(old,new)
old='    if(!Courtyard)return Fail(TEXT("missing CC0 campus HDR environment"));'
new='''    if(!Courtyard)return Fail(TEXT("missing CC0 campus HDR environment: ")+EnvironmentCubemapPath);
    UE_LOG(LogTemp,Display,TEXT("RMSIM_CAMPUS_PRESENTATION glass=%s backdrop=%s cubemap=%s background_brightness=%.3f exposure_ev100=%.3f"),
        *GlassMaterialPath,*EnvironmentMaterialPath,*EnvironmentCubemapPath,BackgroundBrightness,ExposureEV);'''
assert s.count(old)==1;s=s.replace(old,new)
(out/'CampusGym.before.cpp').write_bytes(b)
assert p.read_bytes()==b;p.write_text(s,encoding='utf-8')
cfg=r/'unreal/RMSim/Config/Custom/RMSimGymLumen/DefaultEngine.ini'
c=cfg.read_bytes();t=c.decode('utf-8-sig')
(out/'DefaultEngine.before.ini').write_bytes(c)
assert 'r.Shadow.Virtual.Enable=0' in t
t+='\n; Full native resolution and all-distance clarity for the school gym.\nr.ScreenPercentage=100\nr.ScreenPercentage.Default.Desktop.Mode=0\nr.DynamicRes.OperationMode=0\nr.MaxAnisotropy=16\nr.DepthOfFieldQuality=0\n'
assert cfg.read_bytes()==c;cfg.write_text(t,encoding='utf-8')
sha=lambda x:hashlib.sha256(x).hexdigest()
report={'status':'applied_pending_compile','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':{str(p.relative_to(r)):{'before':sha(b),'after':sha(p.read_bytes())},str(cfg.relative_to(r)):{'before':sha(c),'after':sha(cfg.read_bytes())}},'scope':'Optional validated Arena presentation asset overrides; default material/cubemap paths unchanged when absent. CustomConfig clarity controls only.','perimeter_physics_unchanged':True}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,indent=2))
