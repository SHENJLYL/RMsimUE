using UnrealBuildTool;

public class RMSim : ModuleRules
{
    public RMSim(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine" });
        PrivateDependencyModuleNames.AddRange(new[] { "Json", "RenderCore", "RHI", "InputCore", "PhysicsCore", "Chaos", "Sockets", "Landscape", "Slate", "SlateCore" });
    }
}
