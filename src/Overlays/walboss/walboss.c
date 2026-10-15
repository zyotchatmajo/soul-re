#include "Overlays/walboss/walboss.h"
#include "Game/G2/ANMCTRLR.h"
#include "Game/MATH3D.h"
#include "Game/GAMELOOP.h"
#include "Game/INSTANCE.h"

// this conditional is for the objdiff report
#ifndef SKIP_ASM

void WALBOSS_SetSegmentYaw(Instance *instance, int yaw)
{
    MonsterVars *mv;
    MonsterAttributes *ma;
    WalbossVars *vars;
    WalbossAttributes *attrs;
    G2SVector3 rotation;
    register int value asm("v1");
    int result;

    mv = (MonsterVars *)instance->extraData;
    ma = (MonsterAttributes *)instance->data;
    vars = (WalbossVars *)mv->extraVars;
    attrs = (WalbossAttributes *)ma->tunData;
    if (vars != NULL && attrs != NULL)
    {
        yaw &= 0xFFF;
        vars->segmentYaw = yaw;
        rotation.y = 0;
        rotation.x = 0;
        if (yaw < 0x801)
        {
            value = yaw * 5;
        }
        else
        {
            value = (yaw - 0x1000) * 5;
        }
        result = value * 15;
        __asm__ volatile ("sll %0, %1, 3\n\tsra %0, %0, 10" : "=r"(rotation.z) : "r"(result));
        rotation.z &= 0xFFF;
        G2Anim_SetController_Vector(&instance->anim, *(short *)((char *)attrs + 8), G2ANIM_CTRLRTYPE_ADD_LOCALROT, &rotation);
    }
}

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_InterpSegmentYaw);

void WALBOSS_TurnToPosition(Instance *instance, Position *target)
{
    int angle;
    angle = MATH3D_AngleFromPosToPos(&instance->position, target);
    angle = (short)angle - instance->rotation.z;
    WALBOSS_InterpSegmentYaw(instance, angle & 0xFFF);
}

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_ChooseAttack);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_ShouldIAttack);

void WALBOSS_MessageToLegs(int message)
{
    Instance *inst;
    for (inst = gameTrackerX.instanceList->first; inst != NULL; inst = inst->next)
    {
        if (INSTANCE_Query(inst, 1) == 0x410002)
            INSTANCE_Post(inst, 0x01000017, message);
    }
}

__asm__(
    ".section .text\n"
    ".set noat\n"
    ".set noreorder\n"
    ".globl WALBOSS_LivingLegs\n"
    ".type WALBOSS_LivingLegs, @function\n"
    "WALBOSS_LivingLegs:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_LivingLegs.s\"\n"
    ".set reorder\n"
    ".set at\n"
);

__asm__(
    ".section .text\n"
    ".set noat\n"
    ".set noreorder\n"
    ".globl WALBOSS_FindEgg\n"
    ".type WALBOSS_FindEgg, @function\n"
    "WALBOSS_FindEgg:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_FindEgg.s\"\n"
    ".set reorder\n"
    ".set at\n"
);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_CreateEgg);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_EjectEgg);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_BirthEggEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_BirthEgg);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_CorrectLookAT);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_LegHurtEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_LegHurt);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_HitByEggEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_HitByEgg);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_HandleThorn);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Collide);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Message);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Query);

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", D_88000000);

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", D_88000020);

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", D_8800002C);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Init);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_CleanUp);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_DamageEffect);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_HandleFade);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_IdleEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Idle);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_NoticeEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Notice);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_CombatEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Combat);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_AttackEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Attack);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_HitEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Hit);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_DoNothingEntry);

void WALBOSS_DoNothing() { };

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_DeadEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Dead);

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", func_88000060);

#else 

void WALBOSS_SetSegmentYaw(void) { };   

void WALBOSS_InterpSegmentYaw(void) { };

void WALBOSS_TurnToPosition(void) { };  

void WALBOSS_ChooseAttack(void) { };   

void WALBOSS_ShouldIAttack(void) { };  

void WALBOSS_MessageToLegs(void) { };   

void WALBOSS_LivingLegs(void) { };   

void WALBOSS_FindEgg(void) { };   

void WALBOSS_CreateEgg(void) { };  

void WALBOSS_EjectEgg(void) { }; 

void WALBOSS_BirthEggEntry(void) { };  

void WALBOSS_BirthEgg(void) { };    

void WALBOSS_CorrectLookAT(void) { };   

void WALBOSS_LegHurtEntry(void) { };   

void WALBOSS_LegHurt(void) { };     

void WALBOSS_HitByEggEntry(void) { };  

void WALBOSS_HitByEgg(void) { };    

void WALBOSS_HandleThorn(void) { };  

void WALBOSS_Collide(void) { };   

void WALBOSS_Message(void) { };   

void WALBOSS_Query(void) { };   

void WALBOSS_Init(void) { };     

void WALBOSS_CleanUp(void) { };  

void WALBOSS_DamageEffect(void) { };  

void WALBOSS_HandleFade(void) { };    

void WALBOSS_IdleEntry(void) { };     

void WALBOSS_Idle(void) { };           

void WALBOSS_NoticeEntry(void) { };    

void WALBOSS_Notice(void) { };     

void WALBOSS_CombatEntry(void) { };   

void WALBOSS_Combat(void) { };     

void WALBOSS_AttackEntry(void) { }; 

void WALBOSS_Attack(void) { };  

void WALBOSS_HitEntry(void) { };     

void WALBOSS_Hit(void) { };         

void WALBOSS_DoNothingEntry(void) { };  

void WALBOSS_DoNothing(void) { };     

void WALBOSS_DeadEntry(void) { };  

void WALBOSS_Dead(void) { };        

#endif
