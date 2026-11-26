using UnrealBuildTool;
using System.IO;

public class RMSim : ModuleRules
{
    public RMSim(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        string RepoRoot = Path.GetFullPath(Path.Combine(ModuleDirectory, "../../../.."));
        string MuJoCoPlatform = Target.Platform == UnrealTargetPlatform.Win64 ? "windows-x86_64" : "linux-x86_64";
        string MuJoCoRoot = Path.Combine(RepoRoot, "third_party", "mujoco-sdk", "4.0.1", MuJoCoPlatform);
        string MuJoCoCachedRoot = Path.Combine(RepoRoot, "third_party", "mujoco-sdk", "4.0.1-rmsim-static-world-flex.3", MuJoCoPlatform);
        if (File.Exists(Path.Combine(MuJoCoCachedRoot, "rmsim-build-manifest.json"))
            && File.Exists(Path.Combine(MuJoCoCachedRoot, "c-api", "include", "mujoco", "mujoco.h")))
            MuJoCoRoot = MuJoCoCachedRoot;
        bool WithMuJoCo = (Target.Platform == UnrealTargetPlatform.Win64 || Target.Platform == UnrealTargetPlatform.Linux)
            && File.Exists(Path.Combine(MuJoCoRoot, "c-api", "include", "mujoco", "mujoco.h"));
        PublicDefinitions.Add("RMSIM_WITH_MUJOCO=" + (WithMuJoCo ? "1" : "0"));
        if (WithMuJoCo)
        {
            PrivateIncludePaths.Add(Path.Combine(MuJoCoRoot, "c-api", "include"));
            if (Target.Platform == UnrealTargetPlatform.Win64)
            {
                PublicAdditionalLibraries.Add(Path.Combine(MuJoCoRoot, "lib", "mujoco.lib"));
                PublicDelayLoadDLLs.Add("mujoco.dll");
                RuntimeDependencies.Add("$(BinaryOutputDir)/mujoco.dll", Path.Combine(MuJoCoRoot, "bin", "mujoco.dll"));
            }
            else
            {
                string MuJoCoLibrary = Path.Combine(MuJoCoRoot, "lib", "libmujoco.so.4.0.1");
                PublicAdditionalLibraries.Add(MuJoCoLibrary);
                RuntimeDependencies.Add("$(BinaryOutputDir)/libmujoco.so.4.0.1", MuJoCoLibrary);
                PublicRuntimeLibraryPaths.Add(Path.Combine(MuJoCoRoot, "lib"));
            }
        }
        AddEngineThirdPartyPrivateStaticDependencies(Target, "FreeType2", "UElibPNG", "zlib");
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] { "Json", "ImageCore", "RenderCore", "RHI", "InputCore", "PhysicsCore", "Chaos", "Sockets", "Landscape", "Slate", "SlateCore", "Foliage", "ProceduralMeshComponent", "AudioExtensions" });
    }
}
